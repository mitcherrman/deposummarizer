"""
Synthetic test fixtures. Nothing here is a real legal document.

Page text deliberately avoids the words "page", "exhibit", "affidavit" and
"witness" unless a test means to trip is_page_valid's keyword rule.
"""
import json
import re
from types import SimpleNamespace

import fitz  # PyMuPDF

MARKER_RE = re.compile(r"MKR\d\d")

_FILLER = (
    "Q. Where were you on the morning of the incident? "
    "A. I was at the warehouse on Elm Street with my supervisor. "
    "Q. Did anyone else arrive before nine? A. Two drivers came in early. "
)


def marker(n: int) -> str:
    return f"MKR{n:02d}"


def testimony(n: int) -> str:
    """Long enough (>= 150 chars) to pass both validity checks."""
    return f"{marker(n)} {_FILLER}"


def make_pdf(pages) -> bytes:
    """Build a PDF with one page per entry; None makes a blank page."""
    doc = fitz.open()
    for text in pages:
        page = doc.new_page(width=612, height=792)
        if text:
            # well inside the 8% margins that extraction filters out
            page.insert_textbox(fitz.Rect(90, 90, 520, 700), text, fontsize=11)
    data = doc.tobytes()
    doc.close()
    return data


def pdf_headings_to_markers(pdf_bytes: bytes):
    """
    Read a generated summary PDF and return [(heading_page_number, [markers])]
    in document order, where markers are those printed under each heading.
    """
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    lines = []
    for page in doc:
        lines.extend(page.get_text("text").splitlines())
    doc.close()

    result = []
    for line in lines:
        m = re.fullmatch(r"\s*Page (\d+)\s*", line)
        if m:
            result.append((int(m.group(1)), []))
        elif result:
            result[-1][1].extend(MARKER_RE.findall(line))
    return result


def chat_messages(messages):
    """[(role, content)] for a ChatPromptValue (v1) or a list of dicts (v2)."""
    if hasattr(messages, "to_messages"):
        return [(m.type, m.content) for m in messages.to_messages()]
    return [(m["role"], m["content"]) for m in messages]


def json_payload(messages, key):
    """The first user message that is a JSON object containing `key` (v2), else None."""
    for role, content in chat_messages(messages):
        if role not in ("user", "human"):
            continue
        try:
            data = json.loads(content)
        except (TypeError, ValueError):
            continue
        if isinstance(data, dict) and key in data:
            return data
    return None


class FakeSummaryLLM:
    """
    Stands in for the summary client; echoes the markers it was given.
    v1 requests get the old free-text bullet, v2 requests a valid JSON reply
    built only from the page's own source_page_text.
    """

    def __init__(self):
        self.calls = []

    def invoke(self, messages):
        payload = json_payload(messages, "source_page_text")
        if payload is None:
            user_text = chat_messages(messages)[-1][1]
            found = MARKER_RE.findall(user_text)
            self.calls.append(found)
            return SimpleNamespace(content=f"• Summary of {' '.join(found)}")
        found = MARKER_RE.findall(payload["source_page_text"])
        self.calls.append(found)
        return SimpleNamespace(content=json.dumps(
            {"status": "summarized", "bullets": [f"Summary of {' '.join(found)}"], "uncertain": False}))


class FakeTranslatorLLM:
    def __init__(self):
        self.calls = []

    def invoke(self, messages):
        payload = json_payload(messages, "bullets")
        if payload is None:
            text = messages[-1]["content"]
            self.calls.append(text)
            return SimpleNamespace(content=f"ES {text}")
        self.calls.append(payload["bullets"])
        return SimpleNamespace(content=json.dumps({"bullets": [f"ES {b}" for b in payload["bullets"]]}))
