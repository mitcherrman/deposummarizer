"""
summarizer.py – Deposition → PDF summary (English / Spanish)

• Extracts machine-readable text page-by-page
• Removes header / footer / side margins from each page
• Falls back to OCR (Tesseract via PyMuPDF) if embedded text is poor
• Builds English / Spanish summaries with OpenAI (LangChain)
• Stores finished PDF in session so /out/verify stops polling
"""

import io, base64, logging, time
import fitz  # PyMuPDF

from reportlab.lib.pagesizes import letter
from reportlab.lib.styles  import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus    import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib         import colors

from langchain_openai import ChatOpenAI
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from decouple import config
from django.conf import settings
from importlib import import_module
from server.util import session_lock
import server.summary.deposition_chatbot as cb

# ─────────────────── Session helpers ───────────────────
session_engine = import_module(settings.SESSION_ENGINE)

def update_status_msg(sid: str, msg: str):
    with session_lock:
        s = session_engine.SessionStore(sid)
        if s.exists(sid):
            s["status_msg"] = msg
            s.save()

# Initialize Langchain OpenAI model
OPENAI_KEY = config("OPENAI_KEY").strip()
GPT_MODEL  = config("GPT_MODEL", default="gpt-4o-mini").strip()

llm = ChatOpenAI(openai_api_key=OPENAI_KEY, model_name=GPT_MODEL, temperature=1)
translator_llm = ChatOpenAI(openai_api_key=OPENAI_KEY,
                            model_name="gpt-3.5-turbo-0125")
output_parser = StrOutputParser()

# Define a prompt template for better input to the LLM
def getPrompt(filter_keywords=None, filter_exclude=False):
    systemText = ""
    if filter_keywords == None:
        systemText = """You will be given a section from a legal deposition.
            Provide a brief summary of each page, considering the context of the entire document.
            Format the summary as a list of up to 3 concise bullet points using the round bullet point (utf code 2022).
            Separate bullet points with <br/>.
            """
    else:
        filters = [s.strip() for s in filter_keywords.split(',')]
        if filter_exclude:
            systemText = f"""You will be given a section from a legal deposition.
                Provide a brief summary of each page, considering the context of the entire document.
                Format the summary as a list of up to 3 concise bullet points using the round bullet point (utf code 2022).
                Separate bullet points with <br/>.
                Some details are unimportant. Do not include information related to the following list of keywords in brackets, separated by commas, in your summary:
                [{', '.join(filters)}]
                Include no information that pertains to these keywords. If a page only contains information related to these keywords, then say that no important information is on the page.
                """
        else:
            systemText = f"""You will be given a section from a legal deposition.
                Provide a brief summary of each page, considering the context of the entire document.
                Format the summary as a list of up to 3 concise bullet points using the round bullet point (utf code 2022).
                Separate bullet points with <br/>.
                Only certain details are important. Only include information related to the following list of keywords in brackets, separated by commas, in your summary:
                [{', '.join(filters)}]
                Include no information that does not pertain to these keywords. If a page contains no information related to these keywords, then say that no important information is on the page.
                """
    prompt = ChatPromptTemplate.from_messages([
        ("system", systemText),
        ("user", "{input}")
    ])
    return prompt

def translate_to_spanish(text: str) -> str:
    sys = ("You are a professional translator. Translate the following text "
           "from English to Spanish. Use clear, neutral Spanish and keep line "
           "breaks.")
    prompt = [{"role": "system", "content": sys},
              {"role": "user",   "content": text}]
    return translator_llm.invoke(prompt).content.strip()

# Combine prompt, LLM, and output parser into a chain
def getChain(filter_keywords=None, filter_exclude=False):
    chain = getPrompt(filter_keywords, filter_exclude) | llm | output_parser
    return chain

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

def _extract_clean_text_from_page(page, use_ocr_if_needed=True) -> str:
    """
    Try embedded text → filter margins.
    If short/empty and OCR available → OCR blocks → filter margins.
    """
    # 1) Embedded text blocks
    try:
        native_blocks = page.get_text("blocks")
        txt_native = _blocks_to_text_without_margins(native_blocks, page.rect)
    except Exception:
        txt_native = ""

    if is_page_valid(txt_native):
        return txt_native

    # 2) OCR fallback
    if use_ocr_if_needed and _has_tesseract():
        try:
            tp = page.get_textpage_ocr()  # requires Tesseract installed
            ocr_blocks = page.get_text("blocks", textpage=tp)
            txt_ocr = _blocks_to_text_without_margins(ocr_blocks, page.rect)
            if is_page_valid(txt_ocr):
                return txt_ocr
        except Exception as e:
            logging.warning(f"OCR failed on page {page.number+1}: {e}")

    # 3) Final fallback: plain embedded text (unfiltered), better than nothing
    try:
        txt_plain = page.get_text("text").strip()
        return txt_plain
    except Exception:
        return ""

def extract_text_pages(pdf_buf: io.BytesIO, sid: str):
    """
    Return list[str] – one cleaned (margin-filtered) text per PDF page.
    Uses OCR fallback where needed.
    """
    doc = fitz.open(stream=pdf_buf, filetype="pdf")
    pages, total = [], doc.page_count

    for idx, page in enumerate(doc, start=1):
        # lightweight progress
        if total < 15 or idx % 5 == 1:
            pct = int(idx / total * 100)
            update_status_msg(sid, f"Extracting text… {pct}% ({idx}/{total})")

        text = _extract_clean_text_from_page(page, use_ocr_if_needed=True)
        if is_page_valid(text):
            pages.append(text)
            logging.info(f"✓ page {idx} ({len(text)} chars)")
        else:
            logging.info(f"× page {idx} skipped (no usable text)")

    doc.close()
    logging.info(f"{len(pages)} content pages collected after margin filter / OCR.")
    return pages

# ─────────────────── OpenAI with retries ────────────────────────────────
def _chat_with_retries(client, messages, sid, label,
                       attempts=5, backoff=8) -> str:
    for n in range(1, attempts + 1):
        try:
            return client.invoke(messages).content.strip()
        except Exception as exc:
            wait = backoff * n
            logging.warning(
                f"[{sid}] {label}: {exc} – retry {n}/{attempts} in {wait}s"
            )
            update_status_msg(sid, f"{label}: retry {n}/{attempts}…")
            time.sleep(wait)
    return f"⚠️ {label} failed after {attempts} retries."

# ─────────────────── Summaries ───────────────────────────────────────────
def summarize_deposition(pages, sid: str, target_lang="en", filter_keywords=None, filter_exclude=False):
    """
    Creates one summary record per page and never aborts the whole job.
    Each record is {"en": "...", "es": "..."} depending on target_lang.
    """
    summaries, total = [], len(pages)

    for i, pg in enumerate(pages, start=1):
        update_status_msg(sid, f"{i}/{total} pages processed…")

        if len(pg) < 150:                      # tiny pages → ignore
            continue

        # English summary
        en = _chat_with_retries(
            llm,
            getPrompt(filter_keywords, filter_exclude).invoke({"input": pg[:4000]}),
            sid, f"EN page {i}"
        )

        # Spanish (if requested)
        es = ""
        if target_lang in ("es", "both"):
            es = _chat_with_retries(
                translator_llm,
                [
                    {"role": "system",
                     "content": ("Translate the following text into neutral "
                                 "Spanish. Preserve line breaks.")},
                    {"role": "user", "content": en},
                ],
                sid, f"ES page {i}"
            )

        rec = {}
        if target_lang in ("en", "both"):
            rec["en"] = en
        if target_lang in ("es", "both"):
            rec["es"] = es
        summaries.append(rec)

    return summaries

# ─────────────────── PDF builder ─────────────────────────────────────────
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

    story = []
    for idx, item in enumerate(summaries, start=1):
        story.append(Paragraph(f"Page {idx}", page_style))
        if "en" in item and target_lang in ("en", "both"):
            story.append(Paragraph(item["en"], style_en))
        if "es" in item and target_lang in ("es", "both"):
            story.append(Paragraph(item["es"], style_es))
        story.append(Spacer(1, 8))
    return story

# ─────────────────── Orchestrator ───────────────────────────────────────
def create_summary(pdf_bytes: bytes, sid: str, target_lang="en", filter_keywords=None, filter_exclude=False) -> int:
    """
    End-to-end controller.

    • Always sets session['db_len'] so /out/verify stops polling  
    • Stores summary_pdf unconditionally (even if the Session row was
      missing when the worker thread opened it)
    """
    db_len_value = 0                                  # pessimistic default

    try:
        logging.info(f"→ create_summary({sid}, bytes={len(pdf_bytes)})")
        update_status_msg(sid, "Extracting text 0 %")

        raw_pages = extract_text_pages(io.BytesIO(pdf_bytes), sid)
        pages      = raw_pages[2:]                    # skip cover if desired
        raw_text   = "\n\n".join(raw_pages)
        db_len_value = len(pages)

        # optional chatbot DB
        try:
            update_status_msg(sid, "Configuring chatbot…")
            cb.initBot(raw_text, sid)
        except Exception as e:
            logging.warning(f"[{sid}] chatbot DB skipped: {e}")

        # build summaries
        summaries = summarize_deposition(pages, sid, target_lang, filter_keywords, filter_exclude)

        # build PDF
        update_status_msg(sid, "Building PDF summary…")
        pdf_buf = io.BytesIO()
        write_summaries_to_pdf(summaries, pdf_buf, target_lang)

    except Exception as e:
        logging.exception(f"[{sid}] create_summary crashed")
        update_status_msg(sid, f"❌ Error: {e}")
        db_len_value = 0                               # signal failure

    # ── ALWAYS write results / finish flag ────────────────────────────────
    finally:
        with session_lock:
            s = session_engine.SessionStore(sid)
            # write the PDF only if the run succeeded
            if db_len_value:
                s["summary_pdf"] = base64.b64encode(pdf_buf.getvalue()).decode()
            s["db_len"] = db_len_value                # 0 = failure, >0 = ok
            s.save()

    if db_len_value:
        update_status_msg(sid, "Finished ✓  Ready to download.")
    return db_len_value
