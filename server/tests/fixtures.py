"""
Synthetic test fixtures. Nothing here is a real legal document.

Page text deliberately avoids the words "page", "exhibit", "affidavit" and
"witness" unless a test means to trip is_page_valid's keyword rule.
"""
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


class FakeSummaryLLM:
    """Stands in for ChatOpenAI; echoes the markers it was given."""

    def __init__(self):
        self.calls = []

    def invoke(self, messages):
        user_text = messages.to_messages()[-1].content
        found = MARKER_RE.findall(user_text)
        self.calls.append(found)
        return SimpleNamespace(content=f"• Summary of {' '.join(found)}")


class FakeTranslatorLLM:
    def __init__(self):
        self.calls = []

    def invoke(self, messages):
        text = messages[-1]["content"]
        self.calls.append(text)
        return SimpleNamespace(content=f"ES {text}")
