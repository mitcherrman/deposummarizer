"""
B3 completed-summary workspace on /output: the ready workspace markup and its
server/JS contracts (downloads, PDF preview, chat form, history fragment,
transcript, Clear), chat availability (including the B2 chat_job_id rule and
the 409 presentation), the processing -> workspace hand-over, and the
responsive control architecture.

Sessions are Django cache sessions (server/test_settings.py); no OpenAI, AWS
or Postgres is used. These are behavior/contract tests, not visual snapshots.
"""
import json
import re
import shutil
import subprocess
import time
from pathlib import Path
from unittest import mock

from django.test import SimpleTestCase
from django.urls import resolve

from server import views
from server.tests.test_upload_processing import SessionMixin, parse, visible
from server.tests.test_views import DOCX_TYPE, summary_pdf_b64

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
OUTPUT_JS = STATIC_DIR / "javascript" / "output.js"
PROCESSING_JS = STATIC_DIR / "javascript" / "processing.js"
OUTPUT_CSS = STATIC_DIR / "styles" / "output.css"

JOB = "a" * 32       # synthetic job tokens (never real values)
OTHER = "b" * 32

READY_CHAT = dict(db_len=12, summary_pdf="x", job_id=JOB, chat_job_id=JOB,
                  summary_lang="en", prompt_append=[])
READY_NO_CHAT = dict(db_len=3, summary_pdf="x", job_id=JOB, summary_lang="en", prompt_append=[])


class OutputPage(SessionMixin):
    def page(self, **session):
        if session:
            self.set_session(**session)
        html = self.client.get("/output").content.decode()
        return html, parse(html)

    def workspace(self, root):
        found = root.find_all("section", id="workspace")
        self.assertEqual(len(found), 1)
        return found[0]


# ---------------------------------------------------------------------------
#  Ready workspace markup
# ---------------------------------------------------------------------------
class ReadyWorkspaceTests(OutputPage, SimpleTestCase):
    def setUp(self):
        self.html, self.root = self.page(**READY_CHAT)
        self.ws = self.workspace(self.root)

    def test_workspace_is_the_ready_view(self):
        # revealed by insertIframe(); the processing section is hidden
        self.assertIn("body-container", self.ws.classes)
        self.assertEqual(self.ws.attrs.get("data-chat-state"), "ready")
        self.assertFalse(visible(self.root.find_all("section", id="loading")[0]))
        h1 = self.ws.find_all("h1")
        self.assertEqual([h.all_text().strip() for h in h1], ["Deposition summary"])
        self.assertEqual(h1[0].attrs.get("id"), self.ws.attrs.get("aria-labelledby"))
        self.assertEqual({h.attrs.get("id") for h in self.ws.find_all("h2")},
                         {"summaryPanelTitle", "chatPanelTitle"})

    def test_result_header_uses_only_known_facts(self):
        meta = self.ws.find_all("p", cls="workspace-head__meta")[0].all_text()
        self.assertIn("English", meta)
        self.assertIn("Text read from 12 pages", meta)
        text = self.ws.all_text().lower()
        for invented in ("accuracy", "confidence", "time saved", "citation", "score", "attorney", "privilege"):
            self.assertNotIn(invented, text)

    def test_header_language_and_page_count_wording(self):
        for lang, label in (("es", "Spanish"), ("both", "English and Spanish")):
            with self.subTest(lang=lang):
                _, root = self.page(summary_lang=lang, db_len=1)
                meta = root.find_all("p", cls="workspace-head__meta")[0].all_text()
                self.assertIn(label, meta)
                self.assertIn("Text read from 1 page", meta)
                self.assertNotIn("1 pages", meta)

    def test_ai_assistive_note(self):
        note = self.ws.find_all(id="summaryPanel")[0].find_all("p", cls="ws-panel__note")[0].all_text()
        self.assertIn("AI-generated summary", note)
        self.assertIn("Verify important details against the source transcript", note)

    def test_pdf_preview_targets_out_pdf(self):
        frame = self.ws.find_all(id="docFrame")[0]
        self.assertEqual(frame.attrs.get("data-preview-src"), "/out/pdf")
        self.assertEqual(frame.attrs.get("data-state"), "loading")
        # loading, unsupported and error states exist; the last two are hidden
        states = {m.attrs["data-frame-message"]: m for m in frame.iter() if "data-frame-message" in m.attrs}
        self.assertEqual(set(states), {"loading", "unsupported", "error"})
        self.assertNotIn("hidden", states["loading"].attrs)
        self.assertIn("hidden", states["unsupported"].attrs)
        self.assertEqual(states["error"].attrs.get("role"), "alert")
        self.assertTrue(states["error"].find_all("button", id="retryPreview", type="button"))
        # every fallback offers the PDF directly
        for name in ("unsupported", "error"):
            self.assertTrue([a for a in states[name].find_all("a") if a.attrs.get("href") == "out/pdf"], name)
        # the iframe is created by output.js, never served pre-rendered
        self.assertNotIn("<iframe", self.html)
        self.assertTrue(self.ws.find_all("a", cls="doc-panel__open", href="out/pdf", target="_blank"))

    def test_download_controls(self):
        group = self.ws.find_all(cls="download-group")[0]
        self.assertEqual(group.attrs.get("role"), "group")
        links = group.find_all("a")
        self.assertEqual([(a.attrs.get("href"), a.attrs.get("data-download")) for a in links],
                         [("out/pdf", "pdf"), ("out/docx", "docx")])
        for a in links:
            self.assertIn("download", a.attrs)
            self.assertIn("btn", a.classes)
        texts = [a.all_text() for a in links]
        self.assertIn("PDF", texts[0])
        self.assertIn("Word", texts[1])
        self.assertIn(".docx", texts[1])
        status = self.ws.find_all(id="downloadStatus")[0]
        self.assertEqual((status.attrs.get("role"), status.attrs.get("aria-live")), ("status", "polite"))

    def test_legacy_controls_are_gone(self):
        for legacy in ('id="summary-download-option"', "changeDownloadFormat", "oninput=", "<select",
                       "chat-question-box", "legacy-panel", "clear-button", "Chatbot:", 'style="'):
            self.assertNotIn(legacy, self.html, legacy)

    def test_exactly_one_clear_form_presented_as_start_over(self):
        forms = self.root.find_all("form", id="clearForm")
        self.assertEqual(len(forms), 1)
        form = forms[0]
        self.assertEqual((form.attrs.get("method"), form.attrs.get("action")), ("POST", "/clear"))
        self.assertTrue(form.find_all("input", name="csrfmiddlewaretoken"))
        button = form.find_all("button")[0]
        self.assertEqual((button.attrs.get("type"), button.attrs.get("onclick")), ("button", "clearConfirm()"))
        self.assertIn("Summarize another document", button.all_text())
        self.assertIn("btn-quiet", button.classes)  # secondary, not a red destructive button

    def test_chat_form_contract(self):
        form = self.ws.find_all("form", id="chat-question")[0]
        self.assertIn("chat-question", form.classes)
        names = {n.attrs.get("name") for n in form.iter() if n.attrs.get("name")}
        self.assertEqual(names, {"csrfmiddlewaretoken", "question"})
        field = form.find_all(id="question")[0]
        self.assertEqual(field.tag, "textarea")
        labels = [l for l in form.find_all("label") if l.attrs.get("for") == "question"]
        self.assertEqual(len(labels), 1)
        self.assertTrue(labels[0].all_text().strip())
        send = form.find_all("button", id="askButton")[0]
        self.assertEqual(send.attrs.get("type"), "submit")
        self.assertIn("Ask", send.all_text())
        status = form.find_all(id="chatStatus")[0]
        self.assertEqual((status.attrs.get("role"), status.attrs.get("aria-live")), ("status", "polite"))
        described = field.attrs.get("aria-describedby", "").split()
        self.assertIn("questionError", described)
        self.assertTrue(form.find_all(id="questionError"))
        self.assertNotIn("hidden", form.attrs)

    def test_conversation_region(self):
        log = self.ws.find_all(id="chatMessages")[0]
        self.assertIn("chat-messages", log.classes)
        self.assertEqual(log.attrs.get("role"), "log")
        self.assertEqual(log.all_text().strip(), "")
        empty = self.ws.find_all(cls="chat-empty")[0]
        self.assertNotIn("hidden", empty.attrs)  # no history yet
        self.assertIn("hidden", self.ws.find_all(id="chatUnavailable")[0].attrs)

    def test_transcript_link_follows_history(self):
        link = self.ws.find_all("a", cls="chat-download-button")[0]
        self.assertEqual(link.attrs.get("href"), "transcript")
        self.assertIn("download", link.attrs)
        self.assertIn("hidden", link.attrs)           # no history yet
        _, root = self.page(prompt_append=[{"role": "user", "content": "Q"},
                                           {"role": "assistant", "content": "A"}])
        link = root.find_all("a", cls="chat-download-button")[0]
        self.assertNotIn("hidden", link.attrs)        # restored history -> visible on load
        self.assertIn("hidden", root.find_all(cls="chat-empty")[0].attrs)

    def test_view_switch_controls(self):
        switch = self.ws.find_all(cls="view-switch")[0]
        self.assertEqual(switch.attrs.get("role"), "group")
        buttons = switch.find_all("button")
        self.assertEqual([b.attrs.get("data-view-target") for b in buttons], ["summary", "chat"])
        self.assertEqual([b.attrs.get("aria-pressed") for b in buttons], ["true", "false"])
        for b in buttons:
            self.assertEqual(b.attrs.get("type"), "button")
            self.assertEqual(len(self.root.find_all(id=b.attrs["aria-controls"])), 1)
        self.assertEqual(self.ws.attrs.get("data-view"), "summary")
        self.assertEqual([b.all_text().strip() for b in buttons], ["Summary", "Ask Bear"])
        # no fake tab semantics
        self.assertNotIn('role="tab', self.html)

    def test_scripts_and_b2_hooks(self):
        srcs = [s.attrs.get("src", "") for s in self.root.find_all("script")]
        out = [i for i, s in enumerate(srcs) if s.endswith("javascript/output.js")]
        proc = [i for i, s in enumerate(srcs) if s.endswith("javascript/processing.js")]
        self.assertEqual((len(out), len(proc)), (1, 1))
        self.assertLess(out[0], proc[0])
        for hook in ("loading", "status_msg", "clearForm", "workspace", "question", "chat-question"):
            self.assertEqual(len(self.root.find_all(id=hook)), 1, hook)
        links = [l.attrs.get("href", "") for l in self.root.find_all("link")]
        self.assertTrue(any(h.endswith("/static/styles/output.css") and "//styles" not in h for h in links))

    def test_tokens_never_rendered(self):
        self.assertNotIn(JOB, self.html)
        self.assertNotIn("job_id", self.html)


class ChatAvailabilityTests(OutputPage, SimpleTestCase):
    def test_failed_index_shows_chat_unavailable(self):
        html, root = self.page(**READY_NO_CHAT)
        ws = self.workspace(root)
        self.assertEqual(ws.attrs.get("data-chat-state"), "unavailable")
        panel = ws.find_all(id="chatUnavailable")[0]
        text = panel.all_text()
        self.assertIn("Chat isn't available for this summary", text)
        self.assertIn("Upload the PDF again", text)
        self.assertIn("summary and its downloads still work", text)
        self.assertTrue(panel.find_all("a", href="/home"))
        self.assertNotIn("server error", html.lower())
        self.assertNotIn(JOB, html)
        # the summary side is untouched
        self.assertTrue(ws.find_all(id="docFrame"))
        self.assertEqual(len(ws.find_all("a", cls="summary-download-button")), 2)

    def test_mismatched_chat_marker_is_unavailable(self):
        _, root = self.page(**dict(READY_CHAT, chat_job_id=OTHER))
        self.assertEqual(self.workspace(root).attrs.get("data-chat-state"), "unavailable")

    def test_legacy_summary_without_tokens_keeps_chat(self):
        _, root = self.page(db_len=3, summary_pdf="x", prompt_append=[])
        self.assertEqual(self.workspace(root).attrs.get("data-chat-state"), "ready")

    def test_page_matches_what_ask_would_do(self):
        # chat_available() is presentation only; it must agree with /ask
        cases = [
            dict(READY_CHAT),
            dict(READY_NO_CHAT),
            dict(READY_CHAT, chat_job_id=OTHER),
            dict(db_len=3, summary_pdf="x", prompt_append=[]),
            dict(db_len=0, summary_pdf="x", prompt_append=[]),
        ]
        for case in cases:
            with self.subTest(case=case):
                self.client.cookies.clear()
                self.set_session(**case)
                with mock.patch.object(views, "askQuestion", return_value=["answer", []]) as ask:
                    response = self.client.post("/ask", {"question": "What happened?"})
                expected = views.chat_available(self.client.session)
                self.assertEqual(response.status_code == 200, expected)
                self.assertEqual(ask.called, expected)

    def test_unavailable_409_text_is_the_one_the_page_uses(self):
        self.set_session(**READY_NO_CHAT)
        response = self.client.post("/ask", {"question": "What happened?"})
        self.assertEqual(response.status_code, 409)
        source = OUTPUT_JS.read_text(encoding="utf-8")
        self.assertIn(response.content.decode(), source)


class ProcessingUnaffectedTests(OutputPage, SimpleTestCase):
    def test_running_job_keeps_workspace_hidden_without_meta(self):
        html, root = self.page(db_len=-1, job_started=int(time.time()), job_id=JOB)
        ws = self.workspace(root)
        self.assertIn("hidden", ws.attrs)
        self.assertEqual(ws.attrs.get("data-chat-state"), "pending")
        self.assertFalse(ws.find_all(cls="workspace-head__meta"))
        self.assertTrue(visible(root.find_all("section", id="loading")[0]))
        self.assertNotIn(JOB, html)

    def test_failed_and_empty_states_never_show_the_workspace(self):
        for session in (dict(db_len=0, status_msg="❌ Error: boom"), dict(db_len=-2), {}):
            with self.subTest(session=session):
                self.client.cookies.clear()
                html, root = self.page(**session) if session else self.page()
                self.assertIn("hidden", self.workspace(root).attrs)
                self.assertNotIn("<iframe", html)
                self.assertNotIn("boom", html)
                self.assertEqual(len(root.find_all("form", id="clearForm")), 1)


# ---------------------------------------------------------------------------
#  Server contracts the workspace relies on
# ---------------------------------------------------------------------------
class WorkspaceEndpointTests(SessionMixin, SimpleTestCase):
    def test_workspace_urls_resolve_to_the_existing_views(self):
        for path, view in (("/out/pdf", views.out), ("/out/docx", views.out_docx), ("/chat", views.chat_html),
                           ("/ask", views.ask), ("/transcript", views.transcript), ("/clear", views.clear)):
            self.assertIs(resolve(path).func, view, path)

    def test_preview_probe_and_downloads(self):
        self.set_session(summary_pdf=summary_pdf_b64(), db_len=1)
        self.assertEqual(self.client.head("/out/pdf").status_code, 200)      # preview probe
        pdf = self.client.get("/out/pdf")
        self.assertEqual((pdf.status_code, pdf["Content-Type"]), (200, "application/pdf"))
        self.assertIn("deposition_summary.pdf", pdf["Content-Disposition"])
        docx = self.client.get("/out/docx")
        self.assertEqual((docx.status_code, docx["Content-Type"]), (200, DOCX_TYPE))
        self.assertIn("deposition_summary.docx", docx["Content-Disposition"])
        self.assertTrue(docx.content.startswith(b"PK"))

    def test_preview_probe_fails_without_summary(self):
        self.set_session(db_len=0)
        self.assertNotEqual(self.client.head("/out/pdf").status_code, 200)

    def test_history_fragment_marks_speakers_and_escapes(self):
        self.set_session(prompt_append=[
            {"role": "user", "content": "Who was <b>there</b>?"},
            {"role": "assistant", "content": 'Quote: "Two drivers came in early." <script>alert(1)</script>\nSecond line'},
        ])
        html = self.client.get("/chat").content.decode()
        root = parse(html)
        messages = root.find_all("div", cls="chat-msg")
        self.assertEqual([("chat-msg--user" in m.classes, "chat-msg--bear" in m.classes) for m in messages],
                         [(True, False), (False, True)])
        self.assertEqual([m.find_all("p", cls="chat-msg__who")[0].all_text() for m in messages], ["You", "Bear"])
        self.assertNotIn("<script>", html)
        self.assertNotIn("<b>", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertIn("Second line", messages[1].find_all("p", cls="chat-msg__text")[0].all_text())

    def test_empty_history_fragment_is_empty(self):
        self.assertEqual(self.client.get("/chat").content.decode().strip(), "")

    def test_transcript_format_unchanged(self):
        self.set_session(prompt_append=[{"role": "user", "content": "Q1"}, {"role": "assistant", "content": "A1"}])
        response = self.client.get("/transcript")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response["Content-Type"].startswith("text/plain"))
        self.assertEqual(response.content.decode(), "Q: Q1\nA: A1\n")


# ---------------------------------------------------------------------------
#  Front-end behavior contracts
# ---------------------------------------------------------------------------
class WorkspaceScriptTests(SimpleTestCase):
    def setUp(self):
        self.output_js = OUTPUT_JS.read_text(encoding="utf-8")
        self.processing_js = PROCESSING_JS.read_text(encoding="utf-8")

    def test_insert_iframe_remains_the_global_hand_over(self):
        self.assertRegex(self.output_js, r"(?m)^function insertIframe\(\)")
        self.assertIn('getElementById("loading")', self.output_js)
        self.assertIn("insertIframe();", self.processing_js)       # initial ready state
        # a job finishing while the page is open reloads into the server-rendered workspace
        self.assertIn("window.location.replace(window.location.pathname)", self.processing_js)
        self.assertNotIn("window.onload", self.output_js)

    def test_chat_submit_is_explicit(self):
        self.assertIn('form.addEventListener("submit"', self.output_js)
        self.assertIn("event.preventDefault();", self.output_js)
        self.assertIn('fetch("ask", {method: "POST", body: data', self.output_js)
        self.assertIn("new FormData(form)", self.output_js)
        self.assertIn('fetch("chat"', self.output_js)

    def test_model_text_is_never_parsed_as_html(self):
        # the only innerHTML writes: the server-escaped /chat fragment and static dots
        writes = re.findall(r"(\w+(?:\.\w+)*)\.innerHTML\s*=\s*([^;]+);", self.output_js)
        self.assertEqual(sorted(w[1].strip() for w in writes),
                         sorted(['html', '"<span></span><span></span><span></span>"']))
        self.assertIn("body.textContent = text;", self.output_js)
        self.assertNotIn("innerText", self.output_js)

    def test_helpers(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("Node.js is not installed")
        script = """
const o = require(%s);
const statuses = [409, 400, 403, 500, 502, 0];
console.log(JSON.stringify({
  errors: statuses.map(o.chatErrorFor),
  blanks: ["", "   ", "\\n\\t", null, undefined].map(o.normalizeQuestion),
  kept: o.normalizeQuestion("  Who was there?\\n"),
  names: ["filename=deposition_summary.docx", 'attachment; filename="a b.pdf"', "inline", null,
          "filename*=UTF-8''r%%C3%%A9sum%%C3%%A9.pdf"].map(o.filenameFrom),
  downloads: o.DOWNLOADS
}));
""" % json.dumps(str(OUTPUT_JS))
        result = subprocess.run([node, "-e", script], capture_output=True, text=True, encoding="utf-8", timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        unavailable, empty, session, server_error, gateway, offline = data["errors"]
        self.assertTrue(unavailable.get("unavailable"))
        self.assertIn("Upload the PDF again", unavailable["text"])
        self.assertNotIn("unavailable", server_error)
        for err in (server_error, gateway):
            self.assertIn("try again", err["text"])
            self.assertNotIn("OpenAI", err["text"])            # backend wording never shown
        self.assertIn("connection", offline["text"])
        self.assertIn("Reload", session["text"])
        self.assertEqual(empty["text"], "Type a question first.")
        self.assertEqual(data["blanks"], ["", "", "", "", ""])
        self.assertEqual(data["kept"], "Who was there?")
        self.assertEqual(data["names"], ["deposition_summary.docx", "a b.pdf", None, None, "résumé.pdf"])
        self.assertEqual({k: v["name"] for k, v in data["downloads"].items()},
                         {"pdf": "deposition_summary.pdf", "docx": "deposition_summary.docx"})


class ResponsiveArchitectureTests(SimpleTestCase):
    def setUp(self):
        self.css = OUTPUT_CSS.read_text(encoding="utf-8")

    def block(self, query):
        start = self.css.index("@media " + query)
        depth, i = 0, self.css.index("{", start)
        for j in range(i, len(self.css)):
            depth += {"{": 1, "}": -1}.get(self.css[j], 0)
            if depth == 0:
                return self.css[i:j]
        raise AssertionError(query)

    def test_one_panel_at_a_time_below_1024(self):
        narrow = self.block("(max-width: 1023.98px) {")
        self.assertIn('.workspace[data-view="summary"] .chat-panel', narrow)
        self.assertIn('.workspace[data-view="chat"] .doc-panel', narrow)
        wide = self.block("(min-width: 1024px) {\n  .view-switch")
        self.assertIn(".view-switch { display: none; }", wide)
        self.assertIn("grid-template-columns: minmax(0, 1fr)", wide)

    def test_no_fixed_width_sidebar(self):
        # B0: .chat-container{width:300px} + a non-wrapping flex row overflowed phones
        self.assertIsNone(re.search(r"(?<![-\w])width:\s*\d{3,}px", self.css))
        self.assertNotIn("--cal-", self.css)
        self.assertNotIn(".body-container{", self.css.replace(" ", ""))

    def test_legacy_aliases_and_panel_removed(self):
        for name in ("tokens.css", "base.css", "home.css", "processing.css", "output.css"):
            self.assertNotIn("--cal-", (STATIC_DIR / "styles" / name).read_text(encoding="utf-8"), name)
        self.assertNotIn(".legacy-panel", (STATIC_DIR / "styles" / "base.css").read_text(encoding="utf-8"))
