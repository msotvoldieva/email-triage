"""Classifies one email against the practice's taxonomy via Vertex AI Gemini.

VERTEX AI ONLY -- never the public Gemini Developer API. This is the single
most load-bearing constraint in the whole project
(SPEC-email-triage-core.md: "no third-party LLM vendor, and no
non-BAA-covered Google endpoint, is ever in the data path"). The google-genai
SDK can target either backend depending on how its Client is constructed:
vertexai=True plus a project/location targets Vertex AI, while an alternate
credential-style parameter would target the public API instead. This module
hardcodes vertexai=True in exactly one place (_get_client) and no code path
here ever constructs a client any other way. test_get_client_uses_vertex_ai
and test_classifier_module_never_reads_an_api_key are regression tests
protecting exactly this -- if either breaks, treat it as a compliance
incident, not a flaky test.
"""

import json
import logging
from dataclasses import dataclass
from functools import lru_cache

import google.auth
from google import genai
from google.genai import types

import config
from taxonomy import NEEDS_REVIEW_CATEGORY, Category, Taxonomy

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ClassificationResult:
    category: str
    confidence: float
    needs_review: bool


@lru_cache(maxsize=1)
def _get_client() -> genai.Client:
    _, project_id = google.auth.default()
    return genai.Client(
        vertexai=True,
        project=project_id,
        location=config.load_settings().vertex_ai_location,
    )


def classify(subject: str, body: str, taxonomy: Taxonomy) -> ClassificationResult:
    """Classify one email against the practice's taxonomy via Vertex AI.

    Never logs `subject` or `body`. Falls back to needs_review=True (and
    category=NEEDS_REVIEW_CATEGORY) when confidence is below
    settings.CONFIDENCE_THRESHOLD, or when the model's response fails to
    parse or validate against the taxonomy's category enum.

    Does NOT catch API-level failures (timeouts, 5xx, quota) -- those are
    infrastructure problems, not ambiguous emails, and must not be silently
    disguised as a needs_review classification. The caller (main.py, Task 16)
    is responsible for deciding how to handle them (e.g. per-message
    isolation, matching the pattern already used for Gmail fetch failures).
    """
    settings = config.load_settings()
    real_categories = [c for c in taxonomy.categories if c.name != NEEDS_REVIEW_CATEGORY]

    response = _get_client().models.generate_content(
        model=settings.classifier_model,
        contents=_build_prompt(subject, body, real_categories),
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=_build_response_schema(real_categories),
        ),
    )

    result = _parse_response(response, real_categories, settings.confidence_threshold)

    logger.info(
        "classification.completed",
        extra={
            "category": result.category,
            "confidence": result.confidence,
            "needs_review": result.needs_review,
            # deliberately no subject/body/message content here
        },
    )

    return result


def _build_prompt(subject: str, body: str, categories: list[Category]) -> str:
    category_descriptions = "\n".join(f"- {c.name}: {c.description}" for c in categories)
    return (
        "You are classifying an inbound email for a medical practice's front-desk "
        "triage system. Choose exactly one category from the list below that best "
        "matches the email, and report your confidence in that choice as a number "
        "between 0.0 and 1.0.\n\n"
        f"Categories:\n{category_descriptions}\n\n"
        f"Email subject: {subject}\n\n"
        f"Email body:\n{body}\n"
    )


def _build_response_schema(categories: list[Category]) -> dict:
    return {
        "type": "OBJECT",
        "properties": {
            "category": {"type": "STRING", "enum": [c.name for c in categories]},
            "confidence": {"type": "NUMBER"},
        },
        "required": ["category", "confidence"],
    }


def _parse_response(response, categories: list[Category], threshold: float) -> ClassificationResult:
    valid_names = {c.name for c in categories}

    try:
        data = json.loads(response.text)
    except (ValueError, TypeError, AttributeError):
        return _needs_review_result()

    if not isinstance(data, dict):
        return _needs_review_result()

    category = data.get("category")
    confidence = data.get("confidence")

    if category not in valid_names:
        return _needs_review_result()

    # bool is a subclass of int in Python -- True/False would otherwise pass
    # isinstance(confidence, (int, float)) silently.
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        return _needs_review_result()

    confidence = float(confidence)
    if not (0.0 <= confidence <= 1.0):
        return _needs_review_result()

    if confidence < threshold:
        return ClassificationResult(
            category=NEEDS_REVIEW_CATEGORY, confidence=confidence, needs_review=True
        )

    return ClassificationResult(category=category, confidence=confidence, needs_review=False)


def _needs_review_result() -> ClassificationResult:
    return ClassificationResult(category=NEEDS_REVIEW_CATEGORY, confidence=0.0, needs_review=True)
