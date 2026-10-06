"""
Regression tests for the B0.5 view fixes. Sessions are Django cache sessions
(see server/test_settings.py); no database, OpenAI or AWS is used.
"""
import base64
import io
import re
import zipfile
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory, SimpleTestCase

from server import views
from server.summary import summarizer
from server.tests.fixtures import make_pdf, testimony

DOCX_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def summary_pdf_b64() -> str:
    buf = io.BytesIO()
    summarizer.write_summaries_to_pdf(
        [{"pdf_page": 3, "en": "• First point<br/>• Second point"}], buf, "en")
    return base64.b64encode(buf.getvalue()).decode()


class SessionMixin:
    def set_session(self, **values):
        session = self.client.session
        session.update(values)
        session.save()

    def session_value(self, key, default=None):
        return self.client.session.get(key, default)


class DocxDownloadTests(SessionMixin, SimpleTestCase):
    def test_docx_download_returns_document(self):
        self.set_session(summary_pdf=summary_pdf_b64(), db_len=1)

        response = self.client.get("/out/docx")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], DOCX_TYPE)
        self.assertEqual(response["Content-Disposition"], "filename=deposition_summary.docx")
        with zipfile.ZipFile(io.BytesIO(response.content)) as docx:
            document_xml = docx.read("word/document.xml").decode("utf-8")
        text = re.sub(r"<[^>]+>", "", document_xml)
        self.assertIn("Page 3", text)
        self.assertIn("Second point", text)

    def test_converter_is_closed_separately_from_convert(self):
        self.set_session(summary_pdf=summary_pdf_b64(), db_len=1)
        converter = mock.Mock()
        converter.convert.return_value = None  # as in pdf2docx

        with mock.patch.object(views, "Converter", return_value=converter):
            response = self.client.get("/out/docx")

        self.assertEqual(response.status_code, 200)
        converter.convert.assert_called_once()
        converter.close.assert_called_once_with()

    def test_pdf_download_unchanged(self):
        self.set_session(summary_pdf=summary_pdf_b64(), db_len=1)
        response = self.client.get("/out/pdf")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertEqual(response["Content-Disposition"], "filename=deposition_summary.pdf")

    def test_docx_without_summary_is_409(self):
        self.set_session(db_len=0)
        self.assertEqual(self.client.get("/out/docx").status_code, 409)


class ClearDataTests(SessionMixin, SimpleTestCase):
    def test_clear_removes_uploaded_pdf_and_results(self):
        self.set_session(
            depo_pdf=base64.b64encode(b"%PDF-1.4 synthetic").decode(),
            summary_pdf="c3VtbWFyeQ==", status_msg="done", db_len=4,
            num_docs=1, prompt_append=[{"role": "user", "content": "hi"}],
        )

        response = self.client.post("/clear")

        self.assertRedirects(response, "/home", fetch_redirect_response=False)
        session = self.client.session
        for key in ("depo_pdf", "summary_pdf", "status_msg", "db_len",
                    "num_docs", "prompt_append"):
            self.assertNotIn(key, session)


class DeleteAccountTests(SimpleTestCase):
    def test_anonymous_delete_is_not_an_error(self):
        session = self.client.session
        session["summary_pdf"] = "c3VtbWFyeQ=="
        session.save()

        response = self.client.post("/delete")

        self.assertEqual(response.status_code, 302)
        self.assertTrue(response["Location"].startswith("/login"))
        # an anonymous call must not flush the visitor's session
        self.assertEqual(self.client.session.get("summary_pdf"), "c3VtbWFyeQ==")

    def test_authenticated_delete_logs_out_then_deletes_user(self):
        calls = mock.Mock()
        user = calls.user
        user.is_authenticated = True
        request = RequestFactory().post("/delete")
        request.user = user

        with mock.patch.object(views, "logout", calls.logout):
            response = views.delete_account(request)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/login")
        self.assertEqual(calls.mock_calls, [mock.call.logout(request), mock.call.user.delete()])

    def test_delete_requires_post(self):
        self.assertEqual(self.client.get("/delete").status_code, 405)


class DoubleSubmitTests(SessionMixin, SimpleTestCase):
    def setUp(self):
        patcher = mock.patch.object(views, "Thread")
        self.thread = patcher.start()
        self.addCleanup(patcher.stop)

    def upload(self):
        pdf = SimpleUploadedFile("synthetic.pdf", make_pdf([testimony(1)]),
                                 content_type="application/pdf")
        return self.client.post("/summarize", {"file": pdf, "lang": "en", "filterType": "none"})

    def test_first_upload_starts_one_worker(self):
        response = self.upload()

        self.assertRedirects(response, "/output", fetch_redirect_response=False)
        self.thread.assert_called_once()
        self.thread.return_value.start.assert_called_once()
        self.assertEqual(self.session_value("db_len"), -1)

    def test_second_upload_while_running_does_not_start_another_worker(self):
        self.upload()
        response = self.upload()

        self.assertEqual(response.status_code, 302)
        self.assertTrue(response["Location"].startswith("/output?msg="))
        self.assertEqual(self.thread.call_count, 1)

    def test_running_job_state_is_left_intact(self):
        self.set_session(db_len=-1, depo_pdf="b3JpZ2luYWw=", status_msg="2/9 pages processed…")

        self.upload()

        self.thread.assert_not_called()
        self.assertEqual(self.session_value("db_len"), -1)
        self.assertEqual(self.session_value("depo_pdf"), "b3JpZ2luYWw=")
        self.assertEqual(self.session_value("status_msg"), "2/9 pages processed…")

    def test_new_upload_allowed_after_previous_job_finished(self):
        self.set_session(db_len=5, summary_pdf="b2xk")

        response = self.upload()

        self.assertRedirects(response, "/output", fetch_redirect_response=False)
        self.thread.assert_called_once()
        self.assertNotIn("summary_pdf", self.client.session)
