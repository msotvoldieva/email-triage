# Task 2b (tasks/todo.md, Phase 0): network egress lockdown.
#
# Goal: Cloud Run (once deployed, Task 6) can reach ONLY the six VPC-SC-restricted
# services from infra/vpc_sc.tf, plus Gmail (a separate, non-VPC-SC-governed path via
# IAM/domain-wide delegation -- Gmail traffic isn't routed through this VPC at all,
# it's a direct HTTPS call the Cloud Run runtime makes like any Google API client).
# Nothing else -- no public internet, no arbitrary third-party endpoint -- is reachable
# from inside this network. This is the control VPC-SC's restricted_services doesn't
# provide on its own (see infra/vpc_sc.tf's header comment).
#
# Pattern: route Google API traffic to the *restricted* VIP (199.36.153.4/30), which
# Google's own infrastructure only forwards to services inside a VPC-SC perimeter --
# so even a bug that tried to reach a Google API outside our six-service allowlist
# would fail here, not just get logged. No default internet route exists at all.

resource "google_compute_network" "this" {
  name    = "email-triage-${var.client_name}"
  project = google_project.this.project_id

  auto_create_subnetworks = false

  # Delete the auto-created 0.0.0.0/0 default route immediately. We add back only the
  # one route this network actually needs (the restricted VIP, below).
  delete_default_routes_on_create = true

  depends_on = [google_project_service.apis]
}

# Dedicated subnet for the Serverless VPC Access connector. Deliberately NOT setting
# private_ip_google_access = true here: that flag opens the *default* Private Google
# Access path to ALL Google APIs, which would undermine the six-service allowlist.
# The only path out is the explicit restricted-VIP route + DNS override below.
resource "google_compute_subnetwork" "connector" {
  name    = "email-triage-${var.client_name}-connector"
  project = google_project.this.project_id
  region  = var.region
  network = google_compute_network.this.id

  ip_cidr_range = "10.8.0.0/28"
}

resource "google_vpc_access_connector" "this" {
  name    = "email-triage-${var.client_name}"
  project = google_project.this.project_id
  region  = var.region

  subnet {
    name = google_compute_subnetwork.connector.name
  }

  # Small, fixed range -- matches the spec's "single shared inbox, low volume expected"
  # (SPEC-email-triage-core.md Testing Strategy). Revisit only if real usage demands it.
  min_instances = 2
  max_instances = 3
  machine_type  = "e2-micro"
}

# The one route this network has: Google's restricted VIP for API traffic. Despite the
# next-hop name, Google never actually publishes this range publicly -- traffic to it
# stays inside Google's network and only reaches services inside a VPC-SC perimeter.
resource "google_compute_route" "restricted_vip" {
  name    = "email-triage-${var.client_name}-restricted-vip"
  project = google_project.this.project_id
  network = google_compute_network.this.name

  dest_range       = "199.36.153.4/30"
  next_hop_gateway = "default-internet-gateway"
  priority         = 1000
}

# Override *.googleapis.com to resolve to the restricted VIP instead of Google's
# public IPs, for anything in this VPC (i.e. traffic through the Serverless VPC
# Access connector).
resource "google_dns_managed_zone" "restricted_googleapis" {
  name    = "email-triage-${var.client_name}-restricted-googleapis"
  project = google_project.this.project_id

  dns_name    = "googleapis.com."
  description = "Routes *.googleapis.com to the restricted VIP (199.36.153.4/30) for this VPC only."

  visibility = "private"
  private_visibility_config {
    networks {
      network_url = google_compute_network.this.id
    }
  }
}

resource "google_dns_record_set" "googleapis_cname" {
  project      = google_project.this.project_id
  managed_zone = google_dns_managed_zone.restricted_googleapis.name
  name         = "*.googleapis.com."
  type         = "CNAME"
  ttl          = 300
  rrdatas      = ["restricted.googleapis.com."]
}

resource "google_dns_record_set" "restricted_googleapis_a" {
  project      = google_project.this.project_id
  managed_zone = google_dns_managed_zone.restricted_googleapis.name
  name         = "restricted.googleapis.com."
  type         = "A"
  ttl          = 300
  rrdatas = [
    "199.36.153.4",
    "199.36.153.5",
    "199.36.153.6",
    "199.36.153.7",
  ]
}

# Deny all egress by default; allow only TCP 443 to the restricted VIP. Explicit
# deny-all is defense in depth on top of "no default route exists" -- belt and
# suspenders, since a route misconfiguration shouldn't be the only thing standing
# between this network and the public internet.
resource "google_compute_firewall" "deny_all_egress" {
  name    = "email-triage-${var.client_name}-deny-all-egress"
  project = google_project.this.project_id
  network = google_compute_network.this.name

  direction          = "EGRESS"
  priority           = 65534
  destination_ranges = ["0.0.0.0/0"]

  deny {
    protocol = "all"
  }
}

resource "google_compute_firewall" "allow_restricted_vip_egress" {
  name    = "email-triage-${var.client_name}-allow-restricted-vip"
  project = google_project.this.project_id
  network = google_compute_network.this.name

  direction          = "EGRESS"
  priority           = 1000
  destination_ranges = ["199.36.153.4/30"]

  allow {
    protocol = "tcp"
    ports    = ["443"]
  }
}
