"""
B2 upload + processing: the /summarize form contract, job states shown on
/home and /output, the additive /out/verify headers, stale and duplicate
jobs, sign-in during a job, and the front-end status mapper.

Sessions are Django cache sessions (server/test_settings.py). PDFs are
synthetic; no OpenAI, AWS or Postgres is used.
"""
import json
import re
import shutil
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase

from server import views
from server.summary import summarizer
from server.tests.fixtures import FakeSummaryLLM, FakeTranslatorLLM, make_pdf, testimony
from server.tests.test_shell import TreeBuilder, render
from server.util import session_lock

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
SUMMARIZER_SOURCE = Path(summarizer.__file__).read_text(encoding="utf-8")


def parse(html):
    builder = TreeBuilder()
    builder.feed(html)
    return builder.root


def visible(node):
    """True unless the node or an ancestor (below the gated <body hidden>) is hidden."""
    while node is not None and node.tag != "body":
        if "hidden" in node.attrs:
            return False
        node = node.parent
    return True


class SessionMixin:
    def set_session(self, **values):
        session = self.client.session
        session.update(values)
        session.save()

    def session_value(self, key, default=None):
        return self.client.session.get(key, default)


def pdf_upload(name="synthetic.pdf", data=None):
    return SimpleUploadedFile(name, data if data is not None else make_pdf([testimony(1)]),
                              content_type="application/pdf")


# ---------------------------------------------------------------------------
#  Home form contract
# ---------------------------------------------------------------------------
class HomeFormContractTests(SimpleTestCase):
    def setUp(self):
        self.html, self.root = render("home.html", "/home")
        self.form = self.root.find_all("form", id="form")[0]

    def test_form_posts_multipart_to_summarize(self):
        self.assertEqual(self.form.attrs.get("method"), "POST")
        self.assertEqual(self.form.attrs.get("action"), "/summarize")
        self.assertEqual(self.form.attrs.get("enctype"), "multipart/form-data")
        self.assertTrue(self.form.find_all("input", name="csrfmiddlewaretoken"))

    def test_only_contract_fields_are_named(self):
        # filterText fields are added by home.js as topic chips; the topic
        # entry box itself must never be submitted
        names = {n.attrs.get("name") for n in self.form.iter()
                 if n.tag in ("input", "select", "textarea", "button") and n.attrs.get("name")}
        self.assertEqual(names, {"csrfmiddlewaretoken", "file", "lang", "filterType"})
        self.assertNotIn("name", self.form.find_all("input", id="topicEntry")[0].attrs)

    def test_file_input(self):
        file_input = self.form.find_all("input", name="file")[0]
        self.assertEqual(file_input.attrs.get("type"), "file")
        self.assertEqual(file_input.attrs.get("id"), "fileInput")
        self.assertIn("application/pdf", file_input.attrs.get("accept"))
        self.assertIn("required", file_input.attrs)

    def radios(self, name):
        return [(r.attrs.get("value"), "checked" in r.attrs)
                for r in self.form.find_all("input", name=name)]

    def test_lang_values_and_default(self):
        self.assertEqual(self.radios("lang"), [("en", True), ("es", False), ("both", False)])

    def test_filter_type_values_and_default(self):
        self.assertEqual(self.radios("filterType"),
                         [("none", True), ("include", False), ("exclude", False)])

    def test_every_control_has_a_label(self):
        ids = [n.attrs["id"] for n in self.form.iter()
               if n.tag == "input" and n.attrs.get("type") not in ("hidden",) and n.attrs.get("id")]
        targets = {label.attrs.get("for") for label in self.form.find_all("label")}
        for control_id in ids:
            self.assertIn(control_id, targets)
        self.assertEqual(len(self.form.find_all("legend")), 2)  # language, focus

    def test_submit_is_a_real_submit_button(self):
        button = self.form.find_all("button", id="btnClicked")[0]
        self.assertEqual(button.attrs.get("type"), "submit")
        self.assertNotIn("onclick", button.attrs)

    def test_no_gavel_gif(self):
        self.assertNotIn("gavel", self.html)


class HomeScriptLifecycleTests(SimpleTestCase):
    source = (STATIC_DIR / "javascript" / "home.js").read_text(encoding="utf-8")

    def test_home_js_is_included_once(self):
        html, root = render("home.html", "/home")
        scripts = [s for s in root.find_all("script") if s.attrs.get("src", "").endswith("javascript/home.js")]
        self.assertEqual(len(scripts), 1)
        # after the form it wires up, so its listeners find their elements
        self.assertGreater(html.index("javascript/home.js"), html.index('id="form"'))

    def test_listeners_receive_functions_not_call_results(self):
        # B0: addEventListener("load", checkForSummary()) ran the probe immediately
        self.assertIsNone(re.search(r"addEventListener\(\s*['\"]\w+['\"]\s*,\s*\w+\(\)\s*\)", self.source))
        self.assertNotIn("checkForSummary()", self.source)

    def test_topics_submit_as_repeated_filter_text(self):
        self.assertIn('value.name = "filterText"', self.source)
        self.assertIn('value.type = "hidden"', self.source)

    def test_double_submit_is_blocked_client_side(self):
        self.assertRegex(self.source, r"if \(submitting \|\| !validateForm\(\)\)")


# ---------------------------------------------------------------------------
#  /summarize request contract
# ---------------------------------------------------------------------------
class SummarizeContractTests(SessionMixin, SimpleTestCase):
    def setUp(self):
        patcher = mock.patch.object(views, "Thread")
        self.thread = patcher.start()
        self.addCleanup(patcher.stop)

    def worker_args(self):
        self.thread.assert_called_once()
        sid, lang, pdf_bytes, keywords, exclude, job_id = self.thread.call_args.kwargs["args"]
        self.assertEqual(job_id, self.session_value("job_id"))
        return lang, keywords, exclude

    def test_lang_values_reach_the_worker(self):
        for lang in ("en", "es", "both"):
            with self.subTest(lang=lang):
                self.thread.reset_mock()
                self.client.post("/clear")
                response = self.client.post("/summarize", {"file": pdf_upload(), "lang": lang, "filterType": "none"})
                self.assertEqual(response.status_code, 302)
                self.assertEqual(self.worker_args()[0], lang)
                self.assertEqual(self.session_value("summary_lang"), lang)

    def test_filter_types_and_repeated_filter_text(self):
        cases = {
            "none": ([], False),
            "include": (["medical treatment", "collision details"], False),
            "exclude": (["medical treatment", "collision details"], True),
        }
        for filter_type, (keywords, exclude) in cases.items():
            with self.subTest(filterType=filter_type):
                self.thread.reset_mock()
                self.client.post("/clear")
                self.client.post("/summarize", {
                    "file": pdf_upload(), "lang": "en", "filterType": filter_type,
                    "filterText": ["medical treatment", "collision details"],
                })
                self.assertEqual(self.worker_args()[1:], (keywords, exclude))

    def test_filter_text_sanitizing_keeps_unicode_letters(self):
        # B4 replaced the ASCII-only rule ("niño's café" used to become "nios caf")
        self.client.post("/summarize", {"file": pdf_upload(), "lang": "en", "filterType": "include",
                                        "filterText": ["niño's café", "x-ray 2"]})
        self.assertEqual(self.worker_args()[1], ["niños café", "x-ray 2"])

    def test_invalid_values_are_rejected_before_the_session_changes(self):
        self.set_session(summary_pdf="b2xk", db_len=3)
        for data in ({"lang": "fr", "filterType": "none"}, {"lang": "en", "filterType": "all"}):
            with self.subTest(data=data):
                response = self.client.post("/summarize", dict(data, file=pdf_upload()))
                self.assertEqual(response.status_code, 400)
        self.thread.assert_not_called()
        self.assertEqual(self.session_value("summary_pdf"), "b2xk")

    def test_non_pdf_is_rejected_with_a_message(self):
        self.set_session(summary_pdf="b2xk", db_len=3)
        response = self.client.post("/summarize", {"file": pdf_upload("notes.pdf", b"plain text"),
                                                   "lang": "en", "filterType": "none"})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response["Location"].startswith("/home?msg="))
        self.thread.assert_not_called()
        self.assertEqual(self.session_value("summary_pdf"), "b2xk")

    def test_upload_records_job_start(self):
        before = int(time.time())
        self.client.post("/summarize", {"file": pdf_upload(), "lang": "en", "filterType": "none"})
        self.assertGreaterEqual(self.session_value("job_started"), before)
        self.assertEqual(self.session_value("db_len"), -1)


class WorkerStartTests(SessionMixin, SimpleTestCase):
    """The worker starts after the response, so a fast failure is not overwritten."""

    def test_fast_failure_is_not_overwritten_by_the_upload_response(self):
        def failing_summary(pdf, sid, **kwargs):
            # what create_summary writes when it crashes immediately
            with session_lock:
                s = views.session_engine.SessionStore(sid)
                s["status_msg"] = "❌ Error: boom /internal/path"
                s["db_len"] = 0
                s.save()
            return 0

        class InlineThread:
            def __init__(self, target, args):
                self.target, self.args = target, args

            def start(self):
                self.target(*self.args)  # finish before the client sees the response

        # builtins.print: the worker's "▶" log line can't be encoded on some consoles
        with mock.patch.object(views, "Thread", InlineThread), \
                mock.patch.object(views, "create_summary", failing_summary), \
                mock.patch("builtins.print"):
            response = self.client.post("/summarize", {"file": pdf_upload(), "lang": "en", "filterType": "none"})

        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.session_value("db_len"), 0)
        self.assertEqual(views.job_status(self.client.session), ("failed", "error"))

    def test_worker_is_not_started_until_the_response_closes(self):
        thread = mock.Mock()
        response = views._StartWorkerAfterResponse("/output", thread.start)
        thread.start.assert_not_called()
        response.close()
        response.close()
        thread.start.assert_called_once_with()
        self.assertEqual(response["Location"], "/output")


class DuplicateJobTests(SessionMixin, SimpleTestCase):
    def setUp(self):
        patcher = mock.patch.object(views, "Thread")
        self.thread = patcher.start()
        self.addCleanup(patcher.stop)

    def upload(self):
        return self.client.post("/summarize", {"file": pdf_upload(), "lang": "en", "filterType": "none"})

    def test_guard_rereads_the_stored_session(self):
        # a second request that loaded the session before the first upload saved it
        self.set_session(db_len=5, summary_pdf="b2xk")
        stored = {"db_len": -1, "job_started": int(time.time())}
        engine = SimpleNamespace(SessionStore=lambda sid=None: stored)
        with mock.patch.object(views, "session_engine", engine):
            response = self.upload()
        self.assertTrue(response["Location"].startswith("/output?msg="))
        self.thread.assert_not_called()
        self.assertEqual(self.session_value("summary_pdf"), "b2xk")

    def test_stalled_job_still_blocks_and_explains_recovery(self):
        self.set_session(db_len=-1, job_started=int(time.time()) - views.JOB_STALL_SECONDS - 60)
        response = self.upload()
        self.assertIn("stopped%20responding", response["Location"])
        self.thread.assert_not_called()
        self.assertEqual(self.session_value("db_len"), -1)

    def test_clear_cancels_a_stalled_job_and_allows_a_new_upload(self):
        self.set_session(db_len=-1, job_started=1, status_at=1, status_msg="2/9 pages processed…")
        self.client.post("/clear")
        for key in views.JOB_KEYS:
            self.assertNotIn(key, self.client.session)
        self.assertRedirects(self.upload(), "/output", fetch_redirect_response=False)
        self.thread.assert_called_once()


# ---------------------------------------------------------------------------
#  Job state + /out/verify
# ---------------------------------------------------------------------------
class JobStatusTests(SimpleTestCase):
    def status(self, **session):
        return views.job_status(session)

    def test_states(self):
        now = int(time.time())
        stale = now - views.JOB_STALL_SECONDS - 1
        cases = [
            ({}, ("none", None)),
            ({"db_len": -1, "job_started": now}, ("running", None)),
            ({"db_len": -1, "job_started": stale, "status_at": now}, ("running", None)),
            ({"db_len": -1, "job_started": stale, "status_at": stale}, ("stalled", None)),
            ({"db_len": -1}, ("stalled", None)),  # started before stall tracking existed
            ({"db_len": 4, "summary_pdf": "x"}, ("ready", None)),
            ({"db_len": 0, "status_msg": "Building PDF summary…"}, ("failed", "no-text")),
            ({"db_len": 0, "status_msg": "❌ Error: boom"}, ("failed", "error")),
            ({"db_len": -2, "status_msg": "Error: boom"}, ("failed", "error")),
        ]
        for session, expected in cases:
            with self.subTest(session=session):
                self.assertEqual(self.status(**session), expected)

    def test_status_updates_are_a_heartbeat(self):
        session = summarizer.session_engine.SessionStore()
        session["db_len"] = -1
        session.create()
        session.save()
        before = int(time.time())
        summarizer.update_status_msg(session.session_key, "3/9 pages processed…")
        stored = summarizer.session_engine.SessionStore(session.session_key)
        self.assertGreaterEqual(stored["status_at"], before)
        self.assertEqual(stored["status_msg"], "3/9 pages processed…")


class VerifyEndpointTests(SessionMixin, SimpleTestCase):
    def test_running_keeps_the_existing_contract(self):
        self.set_session(db_len=-1, job_started=int(time.time()), status_msg="3/9 pages processed…")
        response = self.client.get("/out/verify")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content.decode(), "3/9 pages processed…")
        self.assertEqual(response["X-Job-State"], "running")

    def test_stalled_is_still_a_running_response(self):
        self.set_session(db_len=-1, job_started=1)
        response = self.client.get("/out/verify")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["X-Job-State"], "stalled")

    def test_finished_states(self):
        cases = [
            ({"db_len": 3, "summary_pdf": "x"}, "ready", None),
            ({"db_len": 0, "status_msg": "❌ Error: boom"}, "failed", "error"),
            ({"db_len": 0, "status_msg": "Extracting text… 100% (2/2)"}, "failed", "no-text"),
        ]
        for values, state, reason in cases:
            with self.subTest(state=state, reason=reason):
                self.client.post("/clear")
                self.set_session(**values)
                response = self.client.get("/out/verify")
                self.assertEqual(response.status_code, 418)
                self.assertEqual(response.content.decode(), "done")
                self.assertEqual(response["X-Job-State"], state)
                self.assertEqual(response.get("X-Job-Reason"), reason)
                self.assertNotIn("boom", str(response.headers))

    def test_no_session(self):
        response = self.client.get("/out/verify")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response["X-Job-State"], "none")


class CreateSummaryFailureStateTests(SimpleTestCase):
    """Real create_summary on synthetic PDFs ends in a state the UI can explain."""

    def setUp(self):
        for target, value in ((summarizer, "llm"), (summarizer, "translator_llm")):
            patcher = mock.patch.object(target, value,
                                        FakeSummaryLLM() if value == "llm" else FakeTranslatorLLM())
            patcher.start()
            self.addCleanup(patcher.stop)
        for patcher in (mock.patch.object(summarizer.cb, "initBot", mock.Mock()),
                        mock.patch.object(summarizer, "_has_tesseract", lambda: False)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def run_job(self, pdf_bytes):
        session = summarizer.session_engine.SessionStore()
        session.update({"db_len": -1, "job_started": int(time.time())})
        session.create()
        session.save()
        summarizer.create_summary(pdf_bytes, session.session_key)
        return views.job_status(summarizer.session_engine.SessionStore(session.session_key))

    def test_pdf_without_readable_text(self):
        self.assertEqual(self.run_job(make_pdf([None, None])), ("failed", "no-text"))

    def test_unreadable_pdf(self):
        # B4: create_summary extracts through extract_source_pages (every page)
        with mock.patch.object(summarizer, "extract_source_pages", side_effect=RuntimeError("boom")):
            self.assertEqual(self.run_job(make_pdf([testimony(1)])), ("failed", "error"))

    def test_success(self):
        self.assertEqual(self.run_job(make_pdf([testimony(1), testimony(2)])), ("ready", None))


# ---------------------------------------------------------------------------
#  Pages rendered from job state
# ---------------------------------------------------------------------------
class HomeJobCardTests(SessionMixin, SimpleTestCase):
    def card(self):
        root = parse(self.client.get("/home").content.decode())
        cards = root.find_all("section", cls="job-card")
        return cards[0] if cards else None, root

    def test_no_card_without_a_job(self):
        self.assertIsNone(self.card()[0])

    def test_existing_summary_offers_resume(self):
        self.set_session(db_len=2, summary_pdf="x")
        card, _ = self.card()
        self.assertIn("job-card--ready", card.classes)
        self.assertIn("output-url", card.classes)
        self.assertTrue(card.find_all("a", href="/output"))

    def test_running_job_links_to_progress(self):
        self.set_session(db_len=-1, job_started=int(time.time()))
        card, _ = self.card()
        self.assertIn("job-card--running", card.classes)
        self.assertTrue(card.find_all("a", href="/output"))

    def test_stalled_job_offers_cancel(self):
        self.set_session(db_len=-1, job_started=1)
        card, root = self.card()
        self.assertIn("job-card--stalled", card.classes)
        self.assertTrue(card.find_all("button", onclick="clearConfirm()"))
        self.assertEqual(len(root.find_all("form", id="clearForm")), 1)

    def test_failed_job_explains_without_internal_details(self):
        self.set_session(db_len=0, status_msg="❌ Error: boom /internal/path")
        card, _ = self.card()
        self.assertIn("job-card--failed", card.classes)
        self.assertNotIn("boom", card.all_text())


class OutputProcessingStateTests(SessionMixin, SimpleTestCase):
    def panels(self):
        html = self.client.get("/output").content.decode()
        root = parse(html)
        section = root.find_all("section", id="loading")[0]
        shown = [p.attrs["data-panel"] for p in section.find_all() if "data-panel" in p.attrs and visible(p)]
        return html, root, section, shown

    def test_running(self):
        self.set_session(db_len=-1, job_started=int(time.time()))
        _, root, section, shown = self.panels()
        self.assertEqual(shown, ["progress"])
        self.assertFalse(visible(root.find_all(id="stalledNotice")[0]))
        live = root.find_all(id="processingAnnouncer")[0]
        self.assertEqual((live.attrs.get("role"), live.attrs.get("aria-live")), ("status", "polite"))
        self.assertEqual([s.attrs["data-stage"] for s in section.find_all("li", cls="stage")],
                         ["received", "extracting", "indexing", "summarizing", "building"])

    def test_stalled(self):
        self.set_session(db_len=-1, job_started=1)
        _, root, _, shown = self.panels()
        self.assertEqual(shown, ["progress"])
        notice = root.find_all(id="stalledNotice")[0]
        self.assertTrue(visible(notice))
        self.assertTrue(notice.find_all("button", onclick="clearConfirm()"))

    def test_failure_is_an_error_state_not_an_empty_summary(self):
        self.set_session(db_len=0, status_msg="❌ Error: boom /internal/path")
        html, root, _, shown = self.panels()
        self.assertEqual(shown, ["failed"])
        panel = [p for p in root.iter() if p.attrs.get("data-panel") == "failed"][0]
        self.assertEqual(panel.attrs.get("role"), "alert")
        reasons = [p.attrs["data-reason"] for p in panel.find_all("p") if "data-reason" in p.attrs and visible(p)]
        self.assertEqual(reasons, ["error"])
        self.assertTrue(panel.find_all("a", href="/home"))
        self.assertNotIn("boom", html)
        self.assertNotIn("<iframe", html)

    def test_no_text_failure(self):
        self.set_session(db_len=0, status_msg="Building PDF summary…")
        _, root, _, shown = self.panels()
        self.assertEqual(shown, ["failed"])
        reasons = [p.attrs["data-reason"] for p in root.iter() if "data-reason" in p.attrs and visible(p)]
        self.assertEqual(reasons, ["no-text"])

    def test_nothing_to_show(self):
        _, _, _, shown = self.panels()
        self.assertEqual(shown, ["none"])

    def test_ready_goes_straight_to_the_workspace(self):
        self.set_session(db_len=2, summary_pdf="x")
        _, root, section, _ = self.panels()
        self.assertFalse(visible(section))
        self.assertEqual(len(root.find_all(cls="body-container")), 1)

    def test_scripts_and_hooks(self):
        html, root, _, _ = self.panels()
        srcs = [s.attrs.get("src", "") for s in root.find_all("script")]
        output_js = [i for i, s in enumerate(srcs) if s.endswith("javascript/output.js")]
        processing_js = [i for i, s in enumerate(srcs) if s.endswith("javascript/processing.js")]
        self.assertEqual((len(output_js), len(processing_js)), (1, 1))
        self.assertLess(output_js[0], processing_js[0])  # processing.js calls insertIframe()
        for hook in ("status_msg", "loading", "clearForm"):
            self.assertEqual(len(root.find_all(id=hook)), 1, hook)
        self.assertNotIn("gavel", html)


# ---------------------------------------------------------------------------
#  Signing in during a job (B0 S4)
# ---------------------------------------------------------------------------
class SignInDuringJobTests(SessionMixin, SimpleTestCase):
    def sign_in(self):
        def fake_login(request, user):
            request.session.cycle_key()  # what django.contrib.auth.login does
        with mock.patch.object(views, "authenticate", return_value=object()), \
                mock.patch.object(views, "login", side_effect=fake_login):
            return self.client.post("/auth", {"username": "synthetic", "password": "synthetic"})

    def test_running_job_is_cancelled_explicitly(self):
        self.set_session(db_len=-1, job_started=int(time.time()), status_msg="2/9 pages processed…",
                         depo_pdf="b3JpZ2luYWw=", summary_lang="en")
        old_key = self.client.session.session_key

        response = self.sign_in()

        self.assertTrue(response["Location"].startswith("/home?msg=Signing"))
        session = self.client.session
        self.assertNotEqual(session.session_key, old_key)
        for key in views.JOB_KEYS + ("depo_pdf",):
            self.assertNotIn(key, session)
        self.assertEqual(views.job_status(session), ("none", None))
        # the new session can upload again straight away
        with mock.patch.object(views, "Thread") as thread:
            self.client.post("/summarize", {"file": pdf_upload(), "lang": "en", "filterType": "none"})
        thread.assert_called_once()

    def test_sign_in_without_a_job_keeps_the_summary(self):
        self.set_session(db_len=2, summary_pdf="x")
        response = self.sign_in()
        self.assertEqual(response["Location"], "/home")
        self.assertEqual(self.session_value("summary_pdf"), "x")


# ---------------------------------------------------------------------------
#  Front-end status mapper (processing.js), run in Node when available
# ---------------------------------------------------------------------------
class StatusMapperTests(SimpleTestCase):
    PROCESSING_JS = STATIC_DIR / "javascript" / "processing.js"

    def test_mapper_covers_every_status_the_summarizer_emits(self):
        # if a status message changes in summarizer.py, update mapStatus too
        for fragment in ('f"Extracting text… {pct}% ({idx}/{total})"', '"Extracting text 0 %"',
                         '"Configuring chatbot…"', 'f"{i}/{total} pages processed…"',
                         'f"{label}: retry {n}/{attempts}…"', 'f"EN page {page.pdf_page}"',
                         'f"ES page {page.pdf_page}"', '"Building PDF summary…"'):
            self.assertIn(fragment, SUMMARIZER_SOURCE)

    def test_mapping(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("Node.js is not installed")
        script = """
const p = require(%s);
const statuses = %s;
const mapped = statuses.map(p.mapStatus);
let view = {stage: "received"};
const views = [];
for (const s of ["3/40 pages processed…", "EN page 7: retry 2/5…", "Extracting text… 50%% (2/4)", "Building PDF summary…"]) {
  view = p.mergeStatus(view, p.mapStatus(s));
  views.push(view);
}
const described = views.map(p.describe).concat([p.describe({stage: "extracting", percent: 8, current: 1, total: 12}),
                                                 p.describe({stage: "indexing"})]);
console.log(JSON.stringify({mapped, views, described}));
""" % (json.dumps(str(self.PROCESSING_JS)), json.dumps([
            "Working...", "Extracting text 0 %", "Extracting text… 8% (1/12)", "Configuring chatbot…",
            "3/40 pages processed…", "EN page 7: retry 2/5…", "ES page 7: retry 1/5…",
            "Building PDF summary…", "Finished ✓  Ready to download.", "❌ Error: boom", "something else",
        ]))
        result = subprocess.run([node, "-e", script], capture_output=True, text=True, encoding="utf-8", timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)

        self.assertEqual(data["mapped"], [
            {"stage": "received"},
            {"stage": "extracting"},
            {"stage": "extracting", "percent": 8, "current": 1, "total": 12},
            {"stage": "indexing"},
            {"stage": "summarizing", "current": 3, "total": 40},
            {"stage": "summarizing", "retry": 2, "attempts": 5},
            {"stage": "summarizing", "retry": 1, "attempts": 5},
            {"stage": "building"},
            {"stage": "finished"},
            {"stage": None},  # error text is never shown or mapped
            {"stage": None},
        ])
        # a retry keeps the page count; an older stage never moves the view back
        self.assertEqual(data["views"], [
            {"stage": "summarizing", "current": 3, "total": 40},
            {"stage": "summarizing", "current": 3, "total": 40, "retry": 2, "attempts": 5},
            {"stage": "summarizing", "current": 3, "total": 40, "retry": 2, "attempts": 5},
            {"stage": "building"},
        ])
        summarizing, retrying, _, building, extracting, indexing = data["described"]
        # only server-reported numbers: page 3 started = 2 of 40 done
        self.assertEqual((summarizing["line"], summarizing["percent"]), ("Summarizing page 3 of 40", 5))
        self.assertIn("Retrying (attempt 2 of 5)", retrying["detail"])
        self.assertEqual((extracting["line"], extracting["percent"]), ("Extracting text · page 1 of 12", 8))
        self.assertIsNone(building["percent"])   # no server progress -> indeterminate
        self.assertIsNone(indexing["percent"])
