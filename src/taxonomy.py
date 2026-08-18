"""Loads and validates taxonomy/taxonomy.yaml into a typed Taxonomy.

The shipped taxonomy is genuinely provisional -- see taxonomy/taxonomy.yaml's
header and SPEC-email-triage-core.md's Open Questions ("Final
taxonomy/categories"). Swapping in the client's real taxonomy is meant to be a
config change here, not a code change (SPEC-email-triage-core.md Success
Criteria) -- which is exactly why this module validates hard at load time:
a broken config from that future edit should fail loudly on deploy, not
silently on the first email that arrives.
"""

from dataclasses import dataclass
from pathlib import Path

import yaml

# Not something the taxonomy file configures -- always present regardless of
# what's in taxonomy.yaml, so it can't be accidentally dropped by a future edit.
NEEDS_REVIEW_CATEGORY = "needs-review"
_NEEDS_REVIEW_LABEL = "Triage/Needs Review"
_NEEDS_REVIEW_DESCRIPTION = "Confidence below threshold, or no clear category match."

_REQUIRED_FIELDS = ("name", "description", "label")


class TaxonomyValidationError(Exception):
    """Malformed or invalid taxonomy config, raised at load time."""


@dataclass(frozen=True)
class Category:
    name: str
    description: str
    label: str


@dataclass(frozen=True)
class Taxonomy:
    categories: tuple[Category, ...]

    def category_names(self) -> tuple[str, ...]:
        return tuple(c.name for c in self.categories)

    def label_for(self, category_name: str) -> str:
        for category in self.categories:
            if category.name == category_name:
                return category.label
        raise KeyError(f"Unknown category: {category_name}")


def load_taxonomy(path: Path | str) -> Taxonomy:
    path = Path(path)
    raw_text = _read_file(path)
    data = _parse_yaml(raw_text, path)
    raw_categories = _extract_categories_list(data, path)

    categories: list[Category] = []
    seen_names: set[str] = set()

    for index, entry in enumerate(raw_categories):
        category = _parse_category(entry, index, path)
        if category.name in seen_names:
            raise TaxonomyValidationError(f"{path}: duplicate category name '{category.name}'")
        seen_names.add(category.name)
        categories.append(category)

    categories.append(
        Category(
            name=NEEDS_REVIEW_CATEGORY,
            description=_NEEDS_REVIEW_DESCRIPTION,
            label=_NEEDS_REVIEW_LABEL,
        )
    )

    return Taxonomy(categories=tuple(categories))


def _read_file(path: Path) -> str:
    try:
        return path.read_text()
    except OSError as exc:
        raise TaxonomyValidationError(f"Could not read taxonomy file {path}: {exc}") from exc


def _parse_yaml(raw_text: str, path: Path) -> object:
    try:
        return yaml.safe_load(raw_text)
    except yaml.YAMLError as exc:
        raise TaxonomyValidationError(f"Malformed YAML in {path}: {exc}") from exc


def _extract_categories_list(data: object, path: Path) -> list:
    if not isinstance(data, dict) or "categories" not in data:
        raise TaxonomyValidationError(f"{path} must be a mapping with a top-level 'categories' key")

    raw_categories = data["categories"]
    if not isinstance(raw_categories, list) or not raw_categories:
        raise TaxonomyValidationError(f"{path}'s 'categories' must be a non-empty list")

    return raw_categories


def _parse_category(entry: object, index: int, path: Path) -> Category:
    if not isinstance(entry, dict):
        raise TaxonomyValidationError(f"{path}: category at index {index} must be a mapping")

    values = {field: entry.get(field) for field in _REQUIRED_FIELDS}
    missing = [field for field, value in values.items() if not value or not isinstance(value, str)]
    if missing:
        raise TaxonomyValidationError(
            f"{path}: category at index {index} is missing or has an invalid "
            f"value for: {', '.join(missing)}"
        )

    if values["name"] == NEEDS_REVIEW_CATEGORY:
        raise TaxonomyValidationError(
            f"{path}: '{NEEDS_REVIEW_CATEGORY}' is a reserved category name managed by "
            "the code, not the taxonomy file -- remove it from the config"
        )

    return Category(name=values["name"], description=values["description"], label=values["label"])
