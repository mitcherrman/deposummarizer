"""
summarizer.py – Deposition → PDF summary (English / Spanish)

• Extracts machine-readable text page-by-page
• Builds English / Spanish summaries with OpenAI
• Stores finished PDF in session so /out/verify stops polling
"""

import io, base64, logging, time, fitz

import openai                                   # ← works for both 0.x & 1.x
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles  import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus    import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib         import colors

from langchain_openai import ChatOpenAI
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

# ─────────────────── OpenAI clients ─────────────────────
OPENAI_KEY = config("OPENAI_KEY").strip()
GPT_MODEL  = config("GPT_MODEL", default="gpt-4o-mini").strip()
print("OPENAI_KEY seen by app:", repr(OPENAI_KEY[:10] + "…"))

llm = ChatOpenAI(openai_api_key=OPENAI_KEY, model_name=GPT_MODEL)
translator_llm = ChatOpenAI(openai_api_key=OPENAI_KEY,
                            model_name="gpt-3.5-turbo-0125")

def translate_to_spanish(text: str) -> str:
    sys = ("You are a professional translator. Translate the following text "
           "from English to Spanish. Use clear, neutral Spanish and keep line "
           "breaks.")
    prompt = [{"role": "system", "content": sys},
              {"role": "user",   "content": text}]
    return translator_llm.invoke(prompt).content.strip()

# ─────────────────── PDF text extraction ───────────────
KEYWORDS = ["Exhibit", "Affidavit", "Page", "Witness"]

def is_page_valid(txt, min_len=150):
    return len(txt) >= min_len or any(k.lower() in txt.lower() for k in KEYWORDS)

def extract_text_pages(pdf_buf: io.BytesIO, sid: str):
    """Return a list of clean text—one element per PDF page."""
    doc = fitz.open(stream=pdf_buf, filetype="pdf")
    pages, total = [], doc.page_count

    for idx, page in enumerate(doc, start=1):
        if total < 15 or idx % 5 == 1:
            pct = int(idx / total * 100)
            update_status_msg(sid, f"Extracting text… {pct}% ({idx}/{total})")

        text_native = page.get_text("text")
        if is_page_valid(text_native):
            pages.append(text_native.strip())
            logging.info(f"✓ page {idx} ({len(text_native)} chars)")
        else:
            logging.info(f"× page {idx} skipped (no embedded text)")
    doc.close()
    logging.info(f"{len(pages)} content pages collected.")
    return pages


def _chat_with_retries(client, messages, sid, label,
                       attempts=5, backoff=8) -> str:
    for n in range(1, attempts + 1):
        try:
            return client.invoke(messages).content.strip()
        except (openai.OpenAIError, Exception) as exc:         # ← updated
            wait = backoff * n
            logging.warning(
                f"[{sid}] {label}: {exc} – retry {n}/{attempts} in {wait}s"
            )
            update_status_msg(sid, f"{label}: retry {n}/{attempts}…")
            time.sleep(wait)
    return f"⚠️ {label} failed after {attempts} retries."


# ─────────────────── Summaries ────────────────────────────────────────────
def summarize_deposition(pages, sid: str, target_lang="en"):
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
            [
                {"role": "system",
                 "content": ("You will be given part of a legal deposition. "
                              "Provide a concise English summary (≤3 sentences).")},
                {"role": "user", "content": pg[:4000]},
            ],
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
#pdf builder

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

# ─────────────────── Orchestrator ─────────────────────────────────────────
def create_summary(pdf_bytes: bytes, sid: str, target_lang="en") -> int:
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
        summaries = summarize_deposition(pages, sid, target_lang)

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
