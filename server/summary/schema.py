"""
schema.py – structured per-page summary records (BEAR-V2-B4)

One PageSummary exists for every page of the uploaded PDF, in PDF order.
`pdf_page` always comes from the pipeline (1-based index in the uploaded PDF),
never from the model. The model only ever supplies `status` (summarized /
no_relevant_content), up to 3 plain-text bullets and `uncertain`; every reply
is validated here before it can reach a record.

This module has no Django or OpenAI dependency, so the B5 renderer, the
evaluation harness and the tests can import it freely.
"""
import json
import re
import unicodedata
from dataclasses import dataclass, field

# ─────────────────── Page outcomes ───────────────────────────────────────
SUMMARIZED = "summarized"                    # 1–3 bullets from the model
NO_RELEVANT_CONTENT = "no_relevant_content"  # model found nothing relevant (0 bullets)
SKIPPED_NO_TEXT = "skipped_no_text"          # no usable text extracted; no model call
FAILED = "failed"                            # model call or validation failed; no bullets

PAGE_STATUSES = (SUMMARIZED, NO_RELEVANT_CONTENT, SKIPPED_NO_TEXT, FAILED)
MODEL_STATUSES = (SUMMARIZED, NO_RELEVANT_CONTENT)   # the only ones a model may report

# ─────────────────── Translation outcomes ────────────────────────────────
TRANSLATION_NOT_REQUESTED = "not_requested"   # lang = en
TRANSLATION_NOT_APPLICABLE = "not_applicable" # Spanish requested, but no bullets to translate
TRANSLATED = "translated"
TRANSLATION_FAILED = "failed"

TRANSLATION_STATUSES = (TRANSLATION_NOT_REQUESTED, TRANSLATION_NOT_APPLICABLE,
                        TRANSLATED, TRANSLATION_FAILED)

# extraction methods (see summarizer.PageText.method)
EXTRACTION_METHODS = ("native", "ocr", "fallback", "none")

MAX_BULLETS = 3
MAX_BULLET_CHARS = 1000


class SchemaError(ValueError):
    """A model reply that doesn't match the required structure."""


@dataclass(frozen=True)
class PageSummary:
    """
    The pipeline's result for one source PDF page.

    bullets / spanish_bullets are plain text (no markup, no bullet glyphs);
    renderers own all formatting and must escape them. spanish_bullets has the
    same length as bullets when translation_status == "translated".
    """
    pdf_page: int
    status: str
    bullets: tuple = ()
    uncertain: bool = False
    spanish_bullets: tuple = ()
    translation_status: str = TRANSLATION_NOT_REQUESTED
    extraction_method: str = "native"
    source_chars: int = 0        # characters of extracted page text
    summarized_chars: int = 0    # characters actually sent to the model

    def __post_init__(self):
        if not isinstance(self.pdf_page, int) or isinstance(self.pdf_page, bool) or self.pdf_page < 1:
            raise ValueError(f"pdf_page must be a 1-based int, got {self.pdf_page!r}")
        if self.status not in PAGE_STATUSES:
            raise ValueError(f"unknown page status {self.status!r}")
        if self.translation_status not in TRANSLATION_STATUSES:
            raise ValueError(f"unknown translation status {self.translation_status!r}")
        if self.extraction_method not in EXTRACTION_METHODS:
            raise ValueError(f"unknown extraction method {self.extraction_method!r}")
        object.__setattr__(self, "bullets", tuple(self.bullets))
        object.__setattr__(self, "spanish_bullets", tuple(self.spanish_bullets))
        if self.status == SUMMARIZED and not 1 <= len(self.bullets) <= MAX_BULLETS:
            raise ValueError("a summarized page needs 1–3 bullets")
        if self.status != SUMMARIZED and self.bullets:
            raise ValueError(f"a {self.status} page carries no bullets")
        if self.translation_status == TRANSLATED and len(self.spanish_bullets) != len(self.bullets):
            raise ValueError("spanish_bullets must match bullets one-to-one")
        if self.translation_status != TRANSLATED and self.spanish_bullets:
            raise ValueError("spanish_bullets are only kept for a successful translation")

    @property
    def truncated(self) -> bool:
        """True when only the first summarized_chars characters were sent to the model."""
        return 0 < self.summarized_chars < self.source_chars

    def to_dict(self) -> dict:
        """JSON-serializable form (evaluation reports; future renderers)."""
        return {
            "pdf_page": self.pdf_page,
            "status": self.status,
            "bullets": list(self.bullets),
            "uncertain": self.uncertain,
            "spanish_bullets": list(self.spanish_bullets),
            "translation_status": self.translation_status,
            "extraction_method": self.extraction_method,
            "source_chars": self.source_chars,
            "summarized_chars": self.summarized_chars,
            "truncated": self.truncated,
        }


@dataclass(frozen=True)
class ModelSummary:
    """A validated summary reply (no page number: the pipeline attaches it)."""
    status: str
    bullets: tuple = field(default_factory=tuple)
    uncertain: bool = False


# ─────────────────── JSON schemas sent to the provider ───────────────────
# Array/string length limits are enforced by the validators below, not the
# provider schema, because strict structured-output support for those
# keywords varies by model.
SUMMARY_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "status": {
            "type": "string",
            "enum": list(MODEL_STATUSES),
            "description": "summarized if the page has relevant content, otherwise no_relevant_content",
        },
        "bullets": {
            "type": "array",
            "items": {"type": "string"},
            "description": "0 to 3 plain-text bullet sentences; empty when status is no_relevant_content",
        },
        "uncertain": {
            "type": "boolean",
            "description": "true if the page text is garbled, incomplete or ambiguous enough that the summary may be unreliable",
        },
    },
    "required": ["status", "bullets", "uncertain"],
    "additionalProperties": False,
}

TRANSLATION_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "bullets": {
            "type": "array",
            "items": {"type": "string"},
            "description": "one Spanish string per English bullet, same order",
        },
    },
    "required": ["bullets"],
    "additionalProperties": False,
}

# ─────────────────── Bullet text rules ───────────────────────────────────
# HTML / ReportLab mini-markup tags. Other angle-bracket text (for example a
# quoted "<Exhibit 4>") is allowed and escaped by the renderer.
_MARKUP_TAG_RE = re.compile(
    r"<\s*/?\s*(?:br|b|i|u|p|para|font|span|strong|em|a|ul|ol|li|sup|sub|super|strike|s|"
    r"img|script|style|div|h[1-6]|seq|seqreset|ondraw|index|greek|link|unichar|table|tr|td)\b[^>]*>",
    re.IGNORECASE,
)
_LEADING_MARKER_RE = re.compile(r"^(?:[•·▪◦‣∙●*\-–—]\s+)+")
_DIGITS_RE = re.compile(r"\d+")


def clean_bullet(value) -> str:
    """
    Validate one model-written bullet and return it normalized: whitespace
    collapsed, control characters removed, a leading bullet glyph or dash
    dropped. Raises SchemaError for anything that isn't a single plain-text
    bullet (markup, embedded bullet glyphs, empty, overlong).
    """
    if not isinstance(value, str):
        raise SchemaError("bullet is not a string")
    text = "".join(ch for ch in value if ch in "\n\t" or unicodedata.category(ch) != "Cc")
    text = " ".join(text.split())
    text = _LEADING_MARKER_RE.sub("", text).strip()
    if not text:
        raise SchemaError("empty bullet")
    if "•" in text:
        raise SchemaError("bullet contains a bullet glyph (several bullets in one string?)")
    if _MARKUP_TAG_RE.search(text):
        raise SchemaError("bullet contains markup")
    if len(text) > MAX_BULLET_CHARS:
        raise SchemaError("bullet is too long")
    return text


def _load_object(raw, keys) -> dict:
    if not isinstance(raw, str):
        raise SchemaError("reply is not text")
    try:
        data = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise SchemaError(f"reply is not valid JSON ({exc.__class__.__name__})") from None
    if not isinstance(data, dict):
        raise SchemaError("reply is not a JSON object")
    if set(data) != set(keys):
        # also rejects a model-supplied pdf_page: page identity is never the model's
        raise SchemaError(f"reply keys {sorted(data)} != {sorted(keys)}")
    return data


def parse_summary_response(raw) -> ModelSummary:
    """Validate a page-summary reply. Raises SchemaError."""
    data = _load_object(raw, ("status", "bullets", "uncertain"))
    status = data["status"]
    if status not in MODEL_STATUSES:
        raise SchemaError(f"status {status!r} is not allowed")
    if not isinstance(data["uncertain"], bool):
        raise SchemaError("uncertain is not a boolean")
    bullets = data["bullets"]
    if not isinstance(bullets, list):
        raise SchemaError("bullets is not a list")
    if len(bullets) > MAX_BULLETS:
        raise SchemaError("more than 3 bullets")
    bullets = tuple(clean_bullet(b) for b in bullets)
    if status == SUMMARIZED and not bullets:
        raise SchemaError("summarized without bullets")
    if status == NO_RELEVANT_CONTENT and bullets:
        raise SchemaError("no_relevant_content with bullets")
    return ModelSummary(status=status, bullets=bullets, uncertain=data["uncertain"])


def digit_runs(text: str) -> set:
    """Every run of digits in text ("$1,250.00 on 3/4" -> {"1", "250", "00", "3", "4"})."""
    return set(_DIGITS_RE.findall(text))


def parse_translation_response(raw, source_bullets) -> tuple:
    """
    Validate a translation reply against the English bullets it translates:
    same number of bullets, plain text, and every number in each English
    bullet still present (as digits) in its Spanish counterpart.
    Raises SchemaError.
    """
    data = _load_object(raw, ("bullets",))
    bullets = data["bullets"]
    if not isinstance(bullets, list):
        raise SchemaError("bullets is not a list")
    if len(bullets) != len(source_bullets):
        raise SchemaError(f"{len(bullets)} translated bullets for {len(source_bullets)} source bullets")
    cleaned = tuple(clean_bullet(b) for b in bullets)
    for n, (en, es) in enumerate(zip(source_bullets, cleaned), start=1):
        missing = digit_runs(en) - digit_runs(es)
        if missing:
            raise SchemaError(f"bullet {n} lost numbers {sorted(missing)}")
    return cleaned
