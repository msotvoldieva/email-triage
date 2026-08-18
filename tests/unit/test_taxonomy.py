"""Task 12 (tasks/todo.md): taxonomy.py -- load/validate taxonomy.yaml.

Validation must fail at load time, not classification time (SPEC-email-triage-core.md
acceptance criteria) -- every malformed-input test here asserts on load, not on
some later use of the loaded Taxonomy.
"""

from pathlib import Path

import pytest

import taxonomy

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _write_yaml(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "taxonomy.yaml"
    path.write_text(content)
    return path


_VALID_YAML = """
categories:
  - name: billing
    description: Invoices and payments.
    label: Triage/Billing
  - name: scheduling
    description: Appointment requests.
    label: Triage/Scheduling
"""


def test_loads_valid_taxonomy_file(tmp_path):
    path = _write_yaml(tmp_path, _VALID_YAML)

    result = taxonomy.load_taxonomy(path)

    names = result.category_names()
    assert "billing" in names
    assert "scheduling" in names


def test_needs_review_always_present(tmp_path):
    path = _write_yaml(tmp_path, _VALID_YAML)

    result = taxonomy.load_taxonomy(path)

    assert taxonomy.NEEDS_REVIEW_CATEGORY in result.category_names()


def test_needs_review_present_even_with_minimal_taxonomy(tmp_path):
    path = _write_yaml(
        tmp_path,
        "categories:\n  - name: general-inquiry\n    description: Anything else.\n    label: Triage/General\n",
    )

    result = taxonomy.load_taxonomy(path)

    assert taxonomy.NEEDS_REVIEW_CATEGORY in result.category_names()


def test_malformed_yaml_raises_validation_error(tmp_path):
    path = _write_yaml(tmp_path, "categories: [unterminated")

    with pytest.raises(taxonomy.TaxonomyValidationError):
        taxonomy.load_taxonomy(path)


def test_missing_categories_key_raises(tmp_path):
    path = _write_yaml(tmp_path, "not_categories: []\n")

    with pytest.raises(taxonomy.TaxonomyValidationError):
        taxonomy.load_taxonomy(path)


def test_top_level_not_a_mapping_raises(tmp_path):
    path = _write_yaml(tmp_path, "- just\n- a\n- list\n")

    with pytest.raises(taxonomy.TaxonomyValidationError):
        taxonomy.load_taxonomy(path)


def test_categories_not_a_list_raises(tmp_path):
    path = _write_yaml(tmp_path, "categories: not-a-list\n")

    with pytest.raises(taxonomy.TaxonomyValidationError):
        taxonomy.load_taxonomy(path)


def test_empty_categories_list_raises(tmp_path):
    path = _write_yaml(tmp_path, "categories: []\n")

    with pytest.raises(taxonomy.TaxonomyValidationError):
        taxonomy.load_taxonomy(path)


def test_category_entry_not_a_mapping_raises(tmp_path):
    path = _write_yaml(tmp_path, "categories:\n  - just-a-string\n")

    with pytest.raises(taxonomy.TaxonomyValidationError):
        taxonomy.load_taxonomy(path)


def test_category_missing_required_field_raises(tmp_path):
    path = _write_yaml(tmp_path, "categories:\n  - name: billing\n    description: Invoices.\n")

    with pytest.raises(taxonomy.TaxonomyValidationError, match="label"):
        taxonomy.load_taxonomy(path)


def test_duplicate_category_name_raises(tmp_path):
    path = _write_yaml(
        tmp_path,
        """
categories:
  - name: billing
    description: First.
    label: Triage/Billing
  - name: billing
    description: Second.
    label: Triage/Billing2
""",
    )

    with pytest.raises(taxonomy.TaxonomyValidationError, match="duplicate"):
        taxonomy.load_taxonomy(path)


def test_reserved_needs_review_name_in_config_raises(tmp_path):
    path = _write_yaml(
        tmp_path,
        """
categories:
  - name: needs-review
    description: Trying to configure the reserved one.
    label: Triage/Whatever
""",
    )

    with pytest.raises(taxonomy.TaxonomyValidationError, match="reserved"):
        taxonomy.load_taxonomy(path)


def test_missing_file_raises(tmp_path):
    with pytest.raises(taxonomy.TaxonomyValidationError):
        taxonomy.load_taxonomy(tmp_path / "does-not-exist.yaml")


def test_label_for_returns_correct_label(tmp_path):
    path = _write_yaml(tmp_path, _VALID_YAML)
    result = taxonomy.load_taxonomy(path)

    assert result.label_for("billing") == "Triage/Billing"


def test_label_for_unknown_category_raises_keyerror(tmp_path):
    path = _write_yaml(tmp_path, _VALID_YAML)
    result = taxonomy.load_taxonomy(path)

    with pytest.raises(KeyError):
        result.label_for("not-a-real-category")


def test_real_taxonomy_yaml_file_loads_successfully():
    """Guards the actual shipped placeholder file, not just synthetic fixtures --
    a future taxonomy-workshop edit that breaks the schema should fail here, at
    test time, not at deploy time.
    """
    real_path = _REPO_ROOT / "taxonomy" / "taxonomy.yaml"

    result = taxonomy.load_taxonomy(real_path)

    assert taxonomy.NEEDS_REVIEW_CATEGORY in result.category_names()
    assert len(result.categories) > 1
