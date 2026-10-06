"""
Per-job token isolation (B2 correction).

A worker belongs to exactly one upload. After Clear / cancel / sign-in and a
new upload in the same session, a stale worker that wakes up must not touch
the new job: no status, db_len, summary_pdf, num_docs or error writes.

The race is driven through the real views, the real create_summary and the
real session helpers; only the LLMs, the chatbot index and the thread start
are faked. Sessions are Django cache sessions; PDFs are synthetic.
"""
import base64
import time
from types import SimpleNamespace
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase

from server import views
from server.summary import summarizer
from server.tests.fixtures import (
    FakeSummaryLLM, FakeTranslatorLLM, make_pdf, pdf_headings_to_markers, testimony,
)

DOC_A = [testimony(1), testimony(2), testimony(3)]
DOC_B = [testimony(11), testimony(12)]


class CapturedThreads:
    """Stands in for threading.Thread: records each worker instead of starting it."""

    def __init__(self):
        self.jobs = []

    def __call__(self, target, args):
        job = SimpleNamespace(target=target, args=args, start=lambda: None)
        self.jobs.append(job)
        return job

    def run(self, index):
        job = self.jobs[index]
        job.target(*job.args)


class HangingLLM(FakeSummaryLLM):
    """Echoes page markers; the first call 'hangs' while `during_hang` runs."""

    def __init__(self):
        super().__init__()
        self.during_hang = None

    def invoke(self, messages):
        hook, self.during_hang = self.during_hang, None
        if hook:
            hook()
        return super().invoke(messages)


class JobIsolationBase(SimpleTestCase):
    def setUp(self):
        self.threads = CapturedThreads()
        self.llm = HangingLLM()
        patches = [
            mock.patch.object(views, "Thread", self.threads),
            mock.patch.object(summarizer, "llm", self.llm),
            mock.patch.object(summarizer, "translator_llm", FakeTranslatorLLM()),
            mock.patch.object(summarizer.cb, "initBot", mock.Mock()),
            mock.patch.object(summarizer, "_has_tesseract", lambda: False),
            # the worker's "▶" log line can't be encoded on some consoles
            mock.patch("builtins.print"),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def upload(self, pages, lang="en"):
        pdf = SimpleUploadedFile("synthetic.pdf", make_pdf(pages), content_type="application/pdf")
        response = self.client.post("/summarize", {"file": pdf, "lang": lang, "filterType": "none"})
        self.assertEqual(response.status_code, 302)
        return response

    def session_data(self):
        return dict(self.client.session.items())

    def summary_markers(self):
        pdf = base64.b64decode(self.client.session["summary_pdf"])
        return pdf_headings_to_markers(pdf)


class StaleWorkerRaceTests(JobIsolationBase):
    def cancel_and_start_b(self):
        """What the user does while job A hangs: Clear, then upload B."""
        self.client.post("/clear")
        self.upload(DOC_B)
        self.state_b = self.session_data()

    def test_woken_worker_cannot_touch_the_new_job(self):
        self.upload(DOC_A)                      # A. job A starts
        self.llm.during_hang = self.cancel_and_start_b  # B, C. cancel + job B while A hangs

        self.threads.run(0)                     # D. job A wakes up and runs to the end

        # A changed nothing: status, db_len, summary_pdf, counters, timestamps
        self.assertEqual(self.session_data(), self.state_b)
        self.assertEqual(self.client.session["db_len"], -1)
        self.assertNotIn("summary_pdf", self.client.session)
        self.assertNotIn("num_docs", self.client.session)

        self.threads.run(1)                     # E. job B completes normally
        session = self.client.session
        self.assertEqual(session["db_len"], len(DOC_B))
        self.assertEqual(self.summary_markers(), [(1, ["MKR11"]), (2, ["MKR12"])])
        self.assertEqual(session["status_msg"], "Finished ✓  Ready to download.")
        self.assertEqual(views.job_status(session), ("ready", None))

    def test_woken_worker_completion_write_cannot_land_on_the_new_job(self):
        # create_summary returning a page count / raising after the switch:
        # the worker's own completion and error writes must be refused too
        self.upload(DOC_A)
        self.client.post("/clear")
        self.upload(DOC_B)
        state_b = self.session_data()

        with mock.patch.object(views, "create_summary", return_value=7):
            self.threads.run(0)
        self.assertEqual(self.session_data(), state_b)

        with mock.patch.object(views, "create_summary", side_effect=RuntimeError("boom")):
            self.threads.run(0)
        self.assertEqual(self.session_data(), state_b)

    def test_cleared_job_writes_nothing_after_clear(self):
        self.upload(DOC_A)
        self.llm.during_hang = lambda: self.client.post("/clear")
        self.threads.run(0)
        session = self.client.session
        for key in ("db_len", "status_msg", "status_at", "summary_pdf", "job_id", "job_started"):
            self.assertNotIn(key, session)
        self.assertEqual(views.job_status(session), ("none", None))


class SingleJobTests(JobIsolationBase):
    def test_normal_job_is_unchanged(self):
        self.upload(DOC_A, lang="both")
        self.threads.run(0)
        session = self.client.session
        self.assertEqual(session["db_len"], len(DOC_A))
        self.assertEqual(self.summary_markers(),
                         [(1, ["MKR01", "MKR01"]), (2, ["MKR02", "MKR02"]), (3, ["MKR03", "MKR03"])])
        self.assertEqual(session["status_msg"], "Finished ✓  Ready to download.")
        self.assertEqual(views.job_status(session), ("ready", None))
        self.assertEqual(len(self.llm.calls), len(DOC_A))


class JobTokenTests(JobIsolationBase):
    def test_each_upload_gets_a_new_opaque_token(self):
        self.upload(DOC_A)
        first = self.client.session["job_id"]
        self.client.post("/clear")
        self.assertNotIn("job_id", self.client.session)      # H. Clear removes the token
        self.upload(DOC_B)
        second = self.client.session["job_id"]
        self.assertRegex(first, r"^[0-9a-f]{32}$")
        self.assertNotEqual(first, second)
        # the worker gets the token of its own upload
        self.assertEqual(self.threads.jobs[0].args[-1], first)
        self.assertEqual(self.threads.jobs[1].args[-1], second)

    def test_token_is_not_exposed(self):
        self.upload(DOC_A)
        token = self.client.session["job_id"]
        verify = self.client.get("/out/verify")
        self.assertNotIn(token, verify.content.decode() + str(verify.headers))
        for path in ("/home", "/output"):
            self.assertNotIn(token, self.client.get(path).content.decode())

    def test_sign_in_invalidates_the_running_token(self):
        self.upload(DOC_A)
        old_key = self.client.session.session_key

        def fake_login(request, user):
            request.session.cycle_key()  # what django.contrib.auth.login does
        with mock.patch.object(views, "authenticate", return_value=object()), \
                mock.patch.object(views, "login", side_effect=fake_login):
            self.client.post("/auth", {"username": "synthetic", "password": "synthetic"})
        signed_in = self.session_data()
        self.assertNotIn("job_id", signed_in)                # G

        self.threads.run(0)                                  # the old worker wakes up
        self.assertEqual(self.session_data(), signed_in)
        self.assertFalse(summarizer.session_engine.SessionStore().exists(old_key))


class SessionHelperTokenTests(SimpleTestCase):
    def make_session(self, **values):
        session = summarizer.session_engine.SessionStore()
        session.update(values)
        session.create()
        session.save()
        return session.session_key

    def test_race_check_requires_the_current_token(self):
        sid = self.make_session(db_len=-1, job_id="a" * 32)
        self.assertFalse(summarizer.race_check(sid, "a" * 32))
        self.assertTrue(summarizer.race_check(sid, "b" * 32))
        self.assertTrue(summarizer.race_check("missing-session", "a" * 32))
        done = self.make_session(db_len=3, job_id="a" * 32)
        self.assertTrue(summarizer.race_check(done, "a" * 32))

    def test_status_update_requires_the_current_token(self):
        sid = self.make_session(db_len=-1, job_id="b" * 32, status_msg="1/2 pages processed…")
        summarizer.update_status_msg(sid, "9/9 pages processed…", job_id="a" * 32)
        stored = summarizer.session_engine.SessionStore(sid)
        self.assertEqual(stored["status_msg"], "1/2 pages processed…")
        self.assertNotIn("status_at", stored)

        summarizer.update_status_msg(sid, "2/2 pages processed…", job_id="b" * 32)
        stored = summarizer.session_engine.SessionStore(sid)
        self.assertEqual(stored["status_msg"], "2/2 pages processed…")
        self.assertLessEqual(abs(stored["status_at"] - time.time()), 5)
