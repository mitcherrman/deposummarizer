"""
summarizer.py – Deposition → PDF summary (English / Spanish)

• Extracts machine-readable text page-by-page
• Removes header / footer / side margins from each page
• Falls back to OCR (Tesseract via PyMuPDF) if embedded text is poor
• Builds English / Spanish summaries with OpenAI (LangChain)
• Stores finished PDF in session so /out/verify stops polling

Two summary pipelines (settings.SUMMARY_PIPELINE_VERSION):
• v2 (default, B4): one validated PageSummary per PDF page (summary/schema.py);
  pdf_page comes from the pipeline, model replies are structured JSON, failures
  are page state, never summary text.
• v1: the pre-B4 free-text pipeline, kept for one release as a rollback
  switch (remove in B6). It keeps the B0.5 page identity and B2 job tokens.
"""

import io, base64, logging, time
from dataclasses import dataclass
from xml.sax.saxutils import escape
import re

try:
    import pymupdf as fitz   # PyMuPDF ≥ 1.24.3
except ImportError:          # older PyMuPDF only ships the fitz name
    import fitz

from reportlab.lib.pagesizes import letter
from reportlab.lib.styles  import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus    import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib         import colors

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from django.conf import settings
from importlib import import_module
from server.util import session_lock
from server.summary import ai_clients, prompts, schema
from server.summary.schema import PageSummary
import server.summary.deposition_chatbot as cb

# ─────────────────── Session helpers ───────────────────
session_engine = import_module(settings.SESSION_ENGINE)

def current_job_session(sid: str, job_id):
    """
    The stored session for `sid` if `job_id` is still its current job, else
    None. Every worker write goes through this (under session_lock): after
    Clear, sign-in or a new upload the token differs and the write is dropped.
    """
    s = session_engine.SessionStore(sid)
    if s.exists(sid) and s.get("job_id") == job_id:
        return s
    return None

def update_status_msg(sid: str, msg: str, job_id=None):
    with session_lock:
        s = current_job_session(sid, job_id)
        if s is not None:
            s["status_msg"] = msg
            s["status_at"] = int(time.time())   # progress heartbeat (stalled-job detection)
            s.save()

# ─────────────────── AI clients (built lazily) ───────────────────
# Importing this module builds no client and needs no OPENAI_KEY. These hooks
# stay None in production; tests and the evaluation harness assign fakes.
llm = None              # summary client
translator_llm = None   # translation client

def pipeline_version() -> str:
    version = str(getattr(settings, "SUMMARY_PIPELINE_VERSION", "v2")).strip().lower()
    if version not in ("v1", "v2"):
        logging.warning(f"unknown SUMMARY_PIPELINE_VERSION {version!r}; using v2")
        return "v2"
    return version

def _summary_llm(version):
    if llm is not None:
        return llm
    return ai_clients.summary_client() if version == "v2" else ai_clients.legacy_summary_llm()

def _translator_llm(version):
    if translator_llm is not None:
        return translator_llm
    return ai_clients.translation_client() if version == "v2" else ai_clients.legacy_translator_llm()

output_parser = StrOutputParser()

# ─────────────────── v1 prompt (rollback path) ───────────────────
def getPrompt(filter_keywords=None, filter_exclude=False):
    systemText = ""
    if filter_keywords == None or len(filter_keywords) == 0:
        systemText = """You will be given a section from a legal deposition.
            Provide a brief summary of each page, considering the context of the entire document.
            Format the summary as a list of up to 3 concise bullet points using the round bullet point (utf code 2022).
            Separate bullet points with <br/>.
            """
    else:
        if filter_exclude:
            systemText = f"""You will be given a section from a legal deposition.
                Provide a brief summary of each page, considering the context of the entire document.
                Format the summary as a list of up to 3 concise bullet points using the round bullet point (utf code 2022).
                Separate bullet points with <br/>.
                Some details are unimportant. Do not include information related to the following list of keywords in brackets, separated by commas, in your summary:
                [{', '.join(filter_keywords)}]
                Include no information that pertains to these keywords. If a page only contains information related to these keywords, then say that no important information is on the page.
                """
        else:
            systemText = f"""You will be given a section from a legal deposition.
                Provide a brief summary of each page, considering the context of the entire document.
                Format the summary as a list of up to 3 concise bullet points using the round bullet point (utf code 2022).
                Separate bullet points with <br/>.
                Only certain details are important. Only include information related to the following list of keywords in brackets, separated by commas, in your summary:
                [{', '.join(filter_keywords)}]
                Include no information that does not pertain to these keywords. If a page contains no information related to these keywords, then say that no important information is on the page.
                """
    prompt = ChatPromptTemplate.from_messages([
        ("system", systemText),
        ("user", "{input}")
    ])
    return prompt

KEYWORDS = ["Exhibit", "Affidavit", "Page", "Witness"]

# Helper function to determine if a page is valid for processing based on content length or keywords
def is_page_valid(txt: str, min_len: int = 150) -> bool:
    """Heuristic: accept if long enough or contains legal-ish keywords."""
    return len(txt) >= min_len or any(k.lower() in txt.lower() for k in KEYWORDS)

def _has_tesseract() -> bool:
    """Return True if PyMuPDF can find Tesseract data."""
    try:
        return bool(fitz.get_tessdata())
    except Exception:
        return False

def _blocks_to_text_without_margins(blocks, page_rect,
                                    top_ratio=0.08, bottom_ratio=0.08, side_ratio=0.08) -> str:
    """
    Accepts blocks from page.get_text('blocks', ...).
    Filters out header/footer/side blocks, concatenates remaining text.
    """
    top_cut    = page_rect.height * top_ratio
    bottom_cut = page_rect.height * (1 - bottom_ratio)
    left_cut   = page_rect.width  * side_ratio
    right_cut  = page_rect.width  * (1 - side_ratio)

    kept_lines = []

    # blocks are tuples: (x0, y0, x1, y1, text, block_no, block_type)
    for b in blocks or []:
        if not b or len(b) < 5:
            continue
        x0, y0, x1, y1, text = b[:5]
        if not text or not isinstance(text, str):
            continue

        # filter margins
        if y1 <= top_cut:          # header
            continue
        if y0 >= bottom_cut:       # footer
            continue
        if x0 <= left_cut or x1 >= right_cut:  # side margins
            continue

        kept_lines.append(text.strip())

    # Join with single newlines to keep some structure
    return "\n".join([t for t in kept_lines if t])

def _extract_clean_text_from_page(page, use_ocr_if_needed=True) -> tuple[str, str]:
    """
    Try embedded text → filter margins.
    If short/empty and OCR available → OCR blocks → filter margins.
    Returns (text, method): method is native | ocr | fallback | none.
    """
    # 1) Embedded text blocks
    try:
        native_blocks = page.get_text("blocks")
        txt_native = _blocks_to_text_without_margins(native_blocks, page.rect)
    except Exception:
        txt_native = ""

    if is_page_valid(txt_native):
        return txt_native, "native"

    # 2) OCR fallback
    if use_ocr_if_needed and _has_tesseract():
        try:
            tp = page.get_textpage_ocr()  # requires Tesseract installed
            ocr_blocks = page.get_text("blocks", textpage=tp)
            txt_ocr = _blocks_to_text_without_margins(ocr_blocks, page.rect)
            if is_page_valid(txt_ocr):
                return txt_ocr, "ocr"
        except Exception as e:
            logging.warning(f"OCR failed on page {page.number+1}: {e}")

    # 3) Final fallback: plain embedded text (unfiltered), better than nothing
    try:
        txt_plain = page.get_text("text").strip()
    except Exception:
        txt_plain = ""
    return txt_plain, ("fallback" if txt_plain else "none")

@dataclass(frozen=True)
class PageText:
    """Extracted text of one source PDF page."""
    pdf_page: int          # 1-based page number in the uploaded PDF
    text: str
    method: str = "native" # native | ocr | fallback | none (how text was obtained)
    usable: bool = True    # passed is_page_valid: summarized and given to the chatbot

def extract_source_pages(pdf_buf: io.BytesIO, sid: str, job_id=None, should_stop=None) -> list[PageText]:
    """
    Return one PageText per PDF page, in order, tagged with its source page
    number. `usable` marks pages that pass is_page_valid. Uses OCR fallback
    where needed. should_stop (optional) is checked before each page; when it
    returns True the job is stale and JobCancelled is raised.
    """
    doc = fitz.open(stream=pdf_buf, filetype="pdf")
    pages, total = [], doc.page_count

    try:
        for idx, page in enumerate(doc, start=1):
            if should_stop is not None and should_stop():
                raise JobCancelled()
            # lightweight progress
            if total < 15 or idx % 5 == 1:
                pct = int(idx / total * 100)
                update_status_msg(sid, f"Extracting text… {pct}% ({idx}/{total})", job_id)

            text, method = _extract_clean_text_from_page(page, use_ocr_if_needed=True)
            usable = is_page_valid(text)
            pages.append(PageText(pdf_page=idx, text=text, method=method, usable=usable))
            if usable:
                logging.info(f"✓ page {idx} ({len(text)} chars, {method})")
            else:
                logging.info(f"× page {idx} skipped (no usable text)")
    finally:
        doc.close()
    logging.info(f"{sum(p.usable for p in pages)} content pages collected after margin filter / OCR.")
    return pages

def extract_text_pages(pdf_buf: io.BytesIO, sid: str, job_id=None) -> list[PageText]:
    """
    Return list[PageText] – cleaned (margin-filtered) text for every PDF page
    that passes is_page_valid, tagged with its source page number.
    Uses OCR fallback where needed.
    """
    return [p for p in extract_source_pages(pdf_buf, sid, job_id) if p.usable]

# ─────────────────── Job control ────────────────────────────────────────
class JobCancelled(Exception):
    """The job's token is no longer current (Clear, sign-in, new upload, expiry)."""

class ProviderFailure(Exception):
    """A model call still failed after the bounded retries."""

class InvalidModelOutput(Exception):
    """A model reply still failed validation after the repair retry."""

class SummaryFailed(Exception):
    """No page could be summarized: the job fails instead of producing an empty PDF."""

# Check for race condition using session data, returns true if this run must stop:
# the session is gone, another job owns it (job_id differs, e.g. after Clear and
# a new upload) or the job is no longer running
def race_check(sid: str, job_id=None) -> bool:
    s = session_engine.SessionStore(sid)
    try:
        return s.get("job_id") != job_id or s.get("db_len", 0) != -1
    except Exception:
        return True

def _raise_if_stale(sid, job_id):
    if race_check(sid, job_id):
        raise JobCancelled()

def _sleep_unless_stale(seconds, sid, job_id):
    """Back off for `seconds`, checking the job token at least once a second."""
    deadline = time.monotonic() + seconds
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(1.0, remaining))
        _raise_if_stale(sid, job_id)

# ─────────────────── OpenAI with retries ────────────────────────────────
PROVIDER_ATTEMPTS = 5        # requests per call when the provider errors
RETRY_BACKOFF_SECONDS = 8    # wait n × this after the n-th provider error
SCHEMA_ATTEMPTS = 2          # one repair retry for an invalid structured reply

def _report_retry(sid, label, n, attempts, job_id):
    # processing.js maps this text; keep the format
    update_status_msg(sid, f"{label}: retry {n}/{attempts}…", job_id)

def _content_text(response) -> str:
    content = getattr(response, "content", response)
    if isinstance(content, list):   # content blocks
        content = "".join(part.get("text", "") if isinstance(part, dict) else str(part)
                          for part in content)
    if not isinstance(content, str):
        return ""
    return content.strip()

def _chat_with_retries(client, messages, sid, label,
                       attempts=PROVIDER_ATTEMPTS, backoff=None, job_id=None) -> str:
    """
    v1: the reply text. Retries provider errors with a growing, cancellable
    back-off. Raises ProviderFailure when every attempt failed (it never
    returns failure text) and JobCancelled once the job is stale.
    """
    backoff = RETRY_BACKOFF_SECONDS if backoff is None else backoff
    for n in range(1, attempts + 1):
        _raise_if_stale(sid, job_id)
        try:
            return _content_text(client.invoke(messages))
        except Exception as exc:
            logging.warning(f"[{sid}] {label}: {exc.__class__.__name__}: {exc} – attempt {n}/{attempts}")
            if n == attempts:
                raise ProviderFailure(label) from exc
            _report_retry(sid, label, n, attempts, job_id)
            _sleep_unless_stale(backoff * n, sid, job_id)

def _structured_call(client, messages, parse, sid, label, job_id=None):
    """
    v2: one validated structured reply. Provider errors and invalid replies
    draw on separate, bounded budgets (at most PROVIDER_ATTEMPTS +
    SCHEMA_ATTEMPTS - 1 requests). Raises ProviderFailure,
    InvalidModelOutput or JobCancelled; never returns failure text.
    """
    provider_errors = schema_errors = 0
    request = messages
    while True:
        _raise_if_stale(sid, job_id)
        try:
            response = client.invoke(request)
        except Exception as exc:
            provider_errors += 1
            logging.warning(f"[{sid}] {label}: {exc.__class__.__name__}: {exc} – "
                            f"attempt {provider_errors}/{PROVIDER_ATTEMPTS}")
            if provider_errors >= PROVIDER_ATTEMPTS:
                raise ProviderFailure(label) from exc
            _report_retry(sid, label, provider_errors, PROVIDER_ATTEMPTS, job_id)
            _sleep_unless_stale(RETRY_BACKOFF_SECONDS * provider_errors, sid, job_id)
            continue
        try:
            return parse(_content_text(response))
        except schema.SchemaError as exc:
            schema_errors += 1
            logging.warning(f"[{sid}] {label}: invalid structured reply ({exc}) – "
                            f"attempt {schema_errors}/{SCHEMA_ATTEMPTS}")
            if schema_errors >= SCHEMA_ATTEMPTS:
                raise InvalidModelOutput(label) from exc
            request = prompts.with_repair(messages, str(exc))

# ─────────────────── v1 summaries (rollback path) ───────────────────────
def summarize_deposition(pages: list[PageText], sid: str, target_lang="en", filter_keywords=None, filter_exclude=False, job_id=None):
    """
    v1. Creates one summary record per page and never aborts the whole job.
    Each record is {"pdf_page": n, "en": "...", "es": "..."} depending on
    target_lang, where pdf_page is the source page the summary came from,
    or {"pdf_page": n, "failed": True} when the model kept failing.
    """
    summaries, total = [], len(pages)
    client, translator = _summary_llm("v1"), _translator_llm("v1")

    for i, page in enumerate(pages, start=1):
        update_status_msg(sid, f"{i}/{total} pages processed…", job_id)

        pg = page.text
        if len(pg) < 150:                      # tiny pages → ignore
            continue

        # English summary
        try:
            en = _chat_with_retries(
                client,
                getPrompt(filter_keywords, filter_exclude).invoke({"input": pg[:4000]}),
                sid, f"EN page {page.pdf_page}", job_id=job_id
            )
        except ProviderFailure:
            # failure is page state, never summary text
            summaries.append({"pdf_page": page.pdf_page, "failed": True})
            continue
        if race_check(sid, job_id):
            return []

        # Spanish (if requested)
        es = None
        if target_lang in ("es", "both"):
            try:
                es = _chat_with_retries(
                    translator,
                    [
                        {"role": "system",
                         "content": ("Translate the following text into neutral "
                                     "Spanish. Preserve line breaks.")},
                        {"role": "user", "content": en},
                    ],
                    sid, f"ES page {page.pdf_page}", job_id=job_id
                )
            except ProviderFailure:
                es = None
            if race_check(sid, job_id):
                return []

        rec = {"pdf_page": page.pdf_page}
        if target_lang in ("en", "both"):
            rec["en"] = en
        if target_lang in ("es", "both"):
            if es is None:
                rec["es_failed"] = True
            else:
                rec["es"] = es
        summaries.append(rec)

    return summaries

# ─────────────────── v2 structured summaries ────────────────────────────
def _neighbor_context(pages_by_number, pdf_page):
    """(previous tail, next head) of the adjacent usable pages, if enabled."""
    if not getattr(settings, "SUMMARY_NEIGHBOR_CONTEXT", False):
        return None, None
    n = prompts.NEIGHBOR_CONTEXT_CHARS
    prev, nxt = pages_by_number.get(pdf_page - 1), pages_by_number.get(pdf_page + 1)
    previous_tail = prev.text[-n:] if prev is not None and prev.usable else None
    next_head = nxt.text[:n] if nxt is not None and nxt.usable else None
    return previous_tail, next_head

def _page_text_for_model(text: str) -> str:
    """The page text sent to the model: all of it unless the safety cap applies."""
    limit = getattr(settings, "SUMMARY_MAX_PAGE_CHARS", 0) or 0
    if limit > 0 and len(text) > limit:
        return text[:limit]
    return text

def summarize_page_v2(page: PageText, sid, target_lang="en", mode=prompts.FILTER_NONE, topics=(),
                      job_id=None, pages_by_number=None, client=None, translator=None) -> PageSummary:
    """One usable source page → one validated PageSummary."""
    client = client or _summary_llm("v2")
    sent = _page_text_for_model(page.text)
    if len(sent) < len(page.text):
        logging.warning(f"[{sid}] page {page.pdf_page}: {len(page.text)} chars, "
                        f"only the first {len(sent)} sent (SUMMARY_MAX_PAGE_CHARS)")
    record = dict(pdf_page=page.pdf_page, extraction_method=page.method,
                  source_chars=len(page.text), summarized_chars=len(sent))
    previous_tail, next_head = _neighbor_context(pages_by_number or {}, page.pdf_page)
    messages = prompts.build_summary_messages(sent, mode, topics, previous_tail, next_head)

    try:
        result = _structured_call(client, messages, schema.parse_summary_response,
                                  sid, f"EN page {page.pdf_page}", job_id)
    except (ProviderFailure, InvalidModelOutput):
        logging.warning(f"[{sid}] page {page.pdf_page}: recorded as failed")
        return PageSummary(status=schema.FAILED, **record)

    translation = schema.TRANSLATION_NOT_REQUESTED
    spanish = ()
    if target_lang in ("es", "both"):
        if not result.bullets:
            translation = schema.TRANSLATION_NOT_APPLICABLE
        else:
            translator = translator or _translator_llm("v2")
            english = result.bullets
            try:
                spanish = _structured_call(
                    translator, prompts.build_translation_messages(english),
                    lambda raw: schema.parse_translation_response(raw, english),
                    sid, f"ES page {page.pdf_page}", job_id)
                translation = schema.TRANSLATED
            except (ProviderFailure, InvalidModelOutput):
                logging.warning(f"[{sid}] page {page.pdf_page}: translation recorded as failed")
                translation = schema.TRANSLATION_FAILED

    return PageSummary(status=result.status, bullets=result.bullets, uncertain=result.uncertain,
                       spanish_bullets=spanish, translation_status=translation, **record)

def summarize_pages_v2(pages: list[PageText], sid: str, target_lang="en", filter_keywords=None,
                       filter_exclude=False, job_id=None) -> list[PageSummary]:
    """
    One PageSummary for every source page, in PDF order. Unusable pages are
    recorded as skipped_no_text without a model call; a page whose call fails
    is recorded as failed and the job continues. Raises JobCancelled as soon
    as the job is stale (checked before every model request).
    """
    mode, topics = prompts.filter_spec(filter_keywords, filter_exclude)
    pages_by_number = {p.pdf_page: p for p in pages}
    total = sum(p.usable for p in pages)
    client = _summary_llm("v2")
    translator = _translator_llm("v2") if target_lang in ("es", "both") else None

    results, i = [], 0
    for page in pages:
        if not page.usable:
            results.append(PageSummary(pdf_page=page.pdf_page, status=schema.SKIPPED_NO_TEXT,
                                       extraction_method=page.method,
                                       source_chars=len(page.text), summarized_chars=0))
            continue
        i += 1
        _raise_if_stale(sid, job_id)
        update_status_msg(sid, f"{i}/{total} pages processed…", job_id)
        results.append(summarize_page_v2(page, sid, target_lang, mode, topics, job_id,
                                         pages_by_number, client, translator))
    return results

# ─────────────────── PDF builder ─────────────────────────────────────────
# Transitional renderer: B5 replaces it. The pipeline owns every tag here;
# model text is always escaped before it reaches ReportLab markup.
NOTES = {
    "en": {
        schema.NO_RELEVANT_CONTENT: "No relevant content on this page.",
        schema.FAILED: "This page could not be summarized. Review the original page.",
        "translation_failed": "Spanish translation unavailable for this page.",
        "uncertain": "The source text of this page was unclear; verify this summary against the transcript.",
        "truncated": "Only the first {n:,} of {total:,} characters of this page were summarized.",
    },
    "es": {
        schema.NO_RELEVANT_CONTENT: "No hay contenido relevante en esta página.",
        schema.FAILED: "No se pudo resumir esta página. Consulte la página original.",
        "translation_failed": "La traducción al español no está disponible para esta página.",
        "uncertain": "El texto original de esta página no era claro; verifique este resumen con la transcripción.",
        "truncated": "Solo se resumieron los primeros {n} de {total} caracteres de esta página.",
    },
}

_LEGACY_BREAK_RE = re.compile(r"&lt;\s*br\s*/?\s*&gt;", re.IGNORECASE)

def bullets_markup(bullets) -> str:
    """Pipeline-owned bullet glyphs and line breaks around escaped model text."""
    return "<br/>".join(f"• {escape(b)}" for b in bullets)

def legacy_markup(text: str) -> str:
    """v1 text: escaped, keeping only the <br/> the v1 prompt asks for (and newlines) as breaks."""
    return _LEGACY_BREAK_RE.sub("<br/>", escape(text)).replace("\n", "<br/>")

def write_summaries_to_pdf(summaries, out_buf: io.BytesIO, target_lang="en"):
    doc = SimpleDocTemplate(out_buf, pagesize=letter,
                            leftMargin=72, rightMargin=72,
                            topMargin=72, bottomMargin=72)
    doc.build(build_pdf_story(summaries, target_lang))

def build_pdf_story(summaries, target_lang="en"):
    styles = getSampleStyleSheet()
    page_style = ParagraphStyle('Page', parent=styles['Normal'],
                                fontSize=16, leading=16, spaceAfter=12,
                                textColor=colors.HexColor('#007bff'),
                                fontName="Helvetica-Bold")
    style_en = ParagraphStyle('En', parent=styles['Normal'],
                              fontSize=12, leading=14, spaceAfter=6)
    style_es = ParagraphStyle('Es', parent=styles['Normal'],
                              fontSize=11, leading=13,
                              textColor=colors.grey, fontName="Times-Italic",
                              spaceAfter=10)
    style_note = ParagraphStyle('Note', parent=styles['Normal'],
                                fontSize=10, leading=12,
                                textColor=colors.grey, fontName="Helvetica-Oblique",
                                spaceAfter=6)
    notes = NOTES["es" if target_lang == "es" else "en"]

    story = []
    for item in summaries:
        if isinstance(item, PageSummary):
            if item.status == schema.SKIPPED_NO_TEXT:
                continue    # no readable text: no heading (as before B4)
            # heading is the source PDF page, never the position in this list
            story.append(Paragraph(f"Page {item.pdf_page}", page_style))
            if item.status == schema.SUMMARIZED:
                if target_lang in ("en", "both"):
                    story.append(Paragraph(bullets_markup(item.bullets), style_en))
                if target_lang in ("es", "both"):
                    if item.translation_status == schema.TRANSLATED:
                        story.append(Paragraph(bullets_markup(item.spanish_bullets), style_es))
                    else:
                        story.append(Paragraph(escape(notes["translation_failed"]), style_note))
                if item.uncertain:
                    story.append(Paragraph(escape(notes["uncertain"]), style_note))
            else:
                story.append(Paragraph(escape(notes[item.status]), style_note))
            if item.truncated and item.status != schema.FAILED:
                story.append(Paragraph(escape(notes["truncated"].format(
                    n=item.summarized_chars, total=item.source_chars)), style_note))
        else:
            # v1 record
            story.append(Paragraph(f"Page {item['pdf_page']}", page_style))
            if item.get("failed"):
                story.append(Paragraph(escape(notes[schema.FAILED]), style_note))
            if "en" in item and target_lang in ("en", "both"):
                story.append(Paragraph(legacy_markup(item["en"]), style_en))
            if "es" in item and target_lang in ("es", "both"):
                story.append(Paragraph(legacy_markup(item["es"]), style_es))
            if item.get("es_failed") and target_lang in ("es", "both"):
                story.append(Paragraph(escape(notes["translation_failed"]), style_note))
        story.append(Spacer(1, 8))
    return story

# ─────────────────── Orchestrator ───────────────────────────────────────
def _index_chatbot(raw_text, sid, job_id):
    """Build the chatbot index; failure only disables chat for this summary."""
    try:
        update_status_msg(sid, "Configuring chatbot…", job_id)
        # guard runs inside the chatbot's db_lock, before the collection is replaced
        indexed = cb.initBot(raw_text, sid, still_current=lambda: not race_check(sid, job_id))
        if indexed is not None and job_id is not None:
            # chat_job_id proves this job's own index was built while it was current
            with session_lock:
                s = current_job_session(sid, job_id)
                if s is not None:
                    s["chat_job_id"] = job_id
                    s.save()
    except Exception as e:
        logging.warning(f"[{sid}] chatbot DB skipped: {e}")

def _generate(pdf_bytes, sid, target_lang, filter_keywords, filter_exclude, job_id):
    """
    Extraction → chatbot index → page summaries → PDF.
    Returns (db_len, summary PDF bytes or None). Raises JobCancelled when
    the job went stale and any other exception when it crashed.
    """
    version = pipeline_version()
    update_status_msg(sid, "Extracting text 0 %", job_id)

    # every PDF page gets a record; there is no blind cover-page skip
    source_pages = extract_source_pages(io.BytesIO(pdf_bytes), sid, job_id,
                                        should_stop=lambda: race_check(sid, job_id))
    pages = [p for p in source_pages if p.usable]
    raw_text = "\n\n".join(p.text for p in pages)
    # db_len = pages with usable text (what the workspace shows as "Text read
    # from N pages" and the chatbot's k input); skipped pages never count
    db_len_value = len(pages)
    _raise_if_stale(sid, job_id)
    if version == "v2" and not pages:
        return 0, None                      # nothing readable: "no-text" failure

    _index_chatbot(raw_text, sid, job_id)
    _raise_if_stale(sid, job_id)

    if version == "v1":
        summaries = summarize_deposition(pages, sid, target_lang, filter_keywords, filter_exclude, job_id)
    else:
        summaries = summarize_pages_v2(source_pages, sid, target_lang, filter_keywords, filter_exclude, job_id)
        attempted = [s for s in summaries if s.status != schema.SKIPPED_NO_TEXT]
        if attempted and all(s.status == schema.FAILED for s in attempted):
            raise SummaryFailed("no page could be summarized")
    _raise_if_stale(sid, job_id)

    update_status_msg(sid, "Building PDF summary…", job_id)
    pdf_buf = io.BytesIO()
    write_summaries_to_pdf(summaries, pdf_buf, target_lang)
    return db_len_value, (pdf_buf.getvalue() if db_len_value else None)

def create_summary(pdf_bytes: bytes, sid: str, target_lang="en", filter_keywords=None, filter_exclude=False, job_id=None) -> int:
    """
    End-to-end controller.

    • Always sets session['db_len'] so /out/verify stops polling (0 = failure,
      >0 = number of pages with usable text)
    • Stores summary_pdf when the run succeeded
    • Aborts early (returns -1, writes nothing) once the job is stale
    • job_id is the upload's token (session["job_id"]); every status update,
      race check and the final write require it to still be current
    """
    logging.info(f"→ create_summary({sid}, bytes={len(pdf_bytes)}, pipeline={pipeline_version()})")
    try:
        db_len_value, summary_pdf = _generate(pdf_bytes, sid, target_lang,
                                              filter_keywords, filter_exclude, job_id)
    except JobCancelled:
        return -1
    except Exception as e:
        logging.exception(f"[{sid}] create_summary crashed")
        update_status_msg(sid, f"❌ Error: {e}", job_id)
        db_len_value, summary_pdf = 0, None              # signal failure

    # ── write results / finish flag (only while this job is current) ──────
    with session_lock:
        # checked under the lock so Clear / a new upload can't slip in
        # between the check and the write
        if race_check(sid, job_id):
            return -1
        s = session_engine.SessionStore(sid)
        if db_len_value:
            s["summary_pdf"] = base64.b64encode(summary_pdf).decode()
        s["db_len"] = db_len_value                       # 0 = failure, >0 = ok
        s.save()

    if db_len_value:
        update_status_msg(sid, "Finished ✓  Ready to download.", job_id)
    return db_len_value
