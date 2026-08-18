# Setup: Workspace Admin Steps

This is for the **client's Google Workspace administrator**. It has one job:
authorize our service to read and label mail in one shared mailbox, without
ever handing over a password or granting broader access than that.

You don't need to know Terraform, Cloud Run, or anything about how the
pipeline works internally. This should take about 10 minutes.

## Why this looks slightly unusual

Normally, an integration like this runs entirely inside your own Google
Workspace/GCP setup. This one doesn't: the service is hosted in the
**partner's** GCP project, not yours. That's a deliberate choice, not an
oversight — every message this service reads or labels still never leaves
Google's own infrastructure, and it's covered by a separate business
associate agreement between your practice and the partner (ask your point of
contact if you haven't seen this). The only thing that's different from a
same-org setup is *where* the authorization step below happens: you're
authorizing an identity that lives in someone else's Google Cloud project,
not your own. Domain-wide delegation supports this by design — it's the same
mechanism either way.

## What you'll need before starting

- Access to your organization's **Google Admin console** (admin.google.com),
  with permission to manage API controls (usually a Super Admin role)
- The **service account client ID** the partner team hands you — a long
  numeric string, something like `123456789012345678901`. Not a secret; just
  don't post it publicly.
- The **shared mailbox address** this service will watch (e.g.
  `intake@yourpractice.com`) — confirmed with the partner team beforehand.

## Step 1: Confirm the shared mailbox exists

If `intake@yourpractice.com` (or whatever address was agreed on) isn't
already a real mailbox in your Workspace — either a full user account or a
group configured to receive mail — create it now, or ask your Workspace
support to. This should be a dedicated mailbox, not someone's personal work
email, unless the partner team specifically confirmed otherwise with you.

## Step 2: Authorize the service account for domain-wide delegation

1. Go to **admin.google.com** and sign in with your admin account.
2. Navigate to **Security → Access and data control → API controls → Domain-wide
   delegation**. (Google occasionally reorganizes this menu — if these exact
   names don't match what you see, use the Admin console's search bar and
   search "domain-wide delegation".)
3. Click **Add new**.
4. In **Client ID**, paste the numeric ID the partner team gave you.
5. In **OAuth scopes**, paste exactly these two, comma-separated:
   ```
   https://www.googleapis.com/auth/gmail.readonly,https://www.googleapis.com/auth/gmail.modify
   ```
   These scopes let the service read mail and apply/create labels. They do
   **not** allow deleting mail, sending mail as the mailbox, or changing any
   account settings.
6. Click **Authorize**.

That's it — the authorization itself is complete. You should see the client
ID listed with those two scopes when you refresh the page.

## Step 3: Tell the partner team you're done

Let your partner-team contact know Steps 1 and 2 are complete, and confirm
the exact mailbox address you set up in Step 1. They'll handle the remaining
technical step (starting the initial watch on the mailbox) from their side —
there's nothing further for you to do in the Admin console.

## What to expect afterward

Once the partner team confirms the pipeline is live, new mail in the shared
mailbox will start receiving a category label (visible right in Gmail, the
same way any label works) within a few minutes of arrival. Nothing else about
how you use that mailbox changes.

## If something looks wrong

- **No labels are appearing after the partner team said it's live:** contact
  them directly — this is almost never something fixable from the Admin
  console side once Step 2 shows the authorization as complete.
- **You want to revoke access:** delete the client ID entry from the
  Domain-wide Delegation page at any time. This immediately and completely
  cuts off the service's access to the mailbox.
- **You're not sure the scopes were entered correctly:** re-open the entry
  from Step 2 — the two scopes should be listed exactly as shown above, no
  more, no fewer.
