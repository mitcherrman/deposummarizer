"""
Every summary heading must name the PDF page the summary was generated from.

B4: these B0.5 tests now run against the v1 (rollback) pipeline, which must
keep this page identity, including its short-page skip. The structured v2
pipeline gives every page an explicit outcome instead; its equivalents are in
test_summary_pipeline.py.
"""
import base64
import io
from unittest import mock

from django.test import SimpleTestCase, override_settings

from server.summary import summarizer
from server.tests.fixtures import (
    FakeSummaryLLM, FakeTranslatorLLM, make_pdf, marker,
    pdf_headings_to_markers, testimony,
)

COVER = (
    f"{marker(1)} SUPERIOR COURT OF THE STATE OF EXAMPLE\n"
    "JOHN DOE, Plaintiff, v. ACME CORPORATION, Defendant.\n"
    "Case No. 00-0000-TEST\n"
    "DEPOSITION OF JANE ROE, taken on January 1, 2020 at 10:00 a.m."
)

# PDF page -> content
#   1 cover-like caption (substantive, >= 150 chars)
#   2 testimony
#   3 testimony
#   4 blank
#   5 short, no keyword        -> unusable, dropped at extraction
#   6 short, keyword "Exhibit" -> extracted (chatbot sees it), too short to summarize
#   7 testimony
#   8 testimony
MIXED_PAGES = [
    COVER,
    testimony(2),
    testimony(3),
    None,
    f"{marker(5)} Initials: ____",
    f"{marker(6)} Exhibit 12 was marked for identification.",
    testimony(7),
    testimony(8),
]


@override_settings(SUMMARY_PIPELINE_VERSION="v1")
class PageIdentityTestBase(SimpleTestCase):
    def setUp(self):
        self.llm = FakeSummaryLLM()
        self.translator = FakeTranslatorLLM()
        self.init_bot = mock.Mock()
        patches = [
            mock.patch.object(summarizer, "llm", self.llm),
            mock.patch.object(summarizer, "translator_llm", self.translator),
            mock.patch.object(summarizer.cb, "initBot", self.init_bot),
            # deterministic regardless of a local Tesseract install
            mock.patch.object(summarizer, "_has_tesseract", lambda: False),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def run_summary(self, pdf_bytes, lang="en"):
        session = summarizer.session_engine.SessionStore()
        session["db_len"] = -1          # job in progress, as views.summarize sets it
        session.create()
        session.save()
        db_len = summarizer.create_summary(pdf_bytes, session.session_key, target_lang=lang)
        stored = summarizer.session_engine.SessionStore(session.session_key)
        return db_len, stored


class ExtractionTests(PageIdentityTestBase):
    def test_extraction_keeps_source_page_numbers(self):
        pages = summarizer.extract_text_pages(io.BytesIO(make_pdf(MIXED_PAGES)), "no-session")
        self.assertTrue(all(isinstance(p, summarizer.PageText) for p in pages))
        self.assertEqual([p.pdf_page for p in pages], [1, 2, 3, 6, 7, 8])
        for p in pages:
            self.assertIn(marker(p.pdf_page), p.text)


class PdfStoryTests(SimpleTestCase):
    def test_headings_come_from_pdf_page_not_position(self):
        summaries = [{"pdf_page": 9, "en": "first"}, {"pdf_page": 4, "en": "second"}]
        story = summarizer.build_pdf_story(summaries, "en")
        headings = [f.text for f in story if getattr(f, "style", None) is not None
                    and f.style.name == "Page"]
        self.assertEqual(headings, ["Page 9", "Page 4"])


class CreateSummaryPageIdentityTests(PageIdentityTestBase):
    def test_headings_match_source_pages(self):
        db_len, stored = self.run_summary(make_pdf(MIXED_PAGES))

        self.assertGreater(db_len, 0)
        self.assertEqual(stored["db_len"], db_len)
        pdf = base64.b64decode(stored["summary_pdf"])
        mapping = pdf_headings_to_markers(pdf)

        # cover page is summarized (no blind skip); blank, unusable and
        # too-short pages produce no heading; later pages keep their numbers
        self.assertEqual([h for h, _ in mapping], [1, 2, 3, 7, 8])
        for heading, markers in mapping:
            self.assertEqual(markers, [marker(heading)],
                             f"heading 'Page {heading}' summarized {markers}")

        # one LLM call per summarized page, each given only its own page
        self.assertEqual(self.llm.calls, [[marker(n)] for n in (1, 2, 3, 7, 8)])

    def test_chatbot_text_unchanged_all_extracted_pages(self):
        self.run_summary(make_pdf(MIXED_PAGES))
        self.init_bot.assert_called_once()
        raw_text = self.init_bot.call_args.args[0]
        for n in (1, 2, 3, 6, 7, 8):
            self.assertIn(marker(n), raw_text)
        self.assertNotIn(marker(5), raw_text)

    def test_short_leading_cover_is_skipped_without_shifting_numbers(self):
        pdf = make_pdf([f"{marker(1)} CONFIDENTIAL", testimony(2), testimony(3)])
        db_len, stored = self.run_summary(pdf)

        # previously raw_pages[2:] left nothing here and the job failed
        self.assertGreater(db_len, 0)
        mapping = pdf_headings_to_markers(base64.b64decode(stored["summary_pdf"]))
        self.assertEqual(mapping, [(2, [marker(2)]), (3, [marker(3)])])

    def test_bilingual_output_keeps_source_pages(self):
        db_len, stored = self.run_summary(make_pdf(MIXED_PAGES), lang="both")

        mapping = pdf_headings_to_markers(base64.b64decode(stored["summary_pdf"]))
        self.assertEqual([h for h, _ in mapping], [1, 2, 3, 7, 8])
        for heading, markers in mapping:
            # English summary and its Spanish translation sit under the heading
            self.assertEqual(markers, [marker(heading)] * 2)
        self.assertEqual(len(self.translator.calls), 5)
