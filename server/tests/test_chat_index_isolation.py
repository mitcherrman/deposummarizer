"""
Chatbot index isolation (final B2 correction).

collection_<sid> is shared by every job in a session. A stale worker must not
replace it once a newer job owns the session (guard inside db_lock), and chat
is only enabled when chat_job_id proves the active job built its own index.

PGVectorEncrypt is replaced by an in-memory fake that records every mutation;
no Postgres or OpenAI is used. PDFs are synthetic.
"""
import re
import threading
from unittest import mock

from django.test import SimpleTestCase

from server import views
from server.summary import deposition_chatbot as cb
from server.tests.fixtures import testimony
from server.tests.test_job_isolation import DOC_A, DOC_B, JobIsolationBase

REAL_INIT_BOT = cb.initBot   # captured before any test patches it


class FakeVectorStore:
    """Stands in for PGVectorEncrypt: collections are lists of chunks."""
    collections = {}
    log = []
    hold = None          # threading.Event: block add_texts until set
    entered = None       # threading.Event: set once add_texts is reached
    fail = False

    def __init__(self, collection_name, pre_delete_collection=False, **kwargs):
        self.name = collection_name
        FakeVectorStore.log.append(("construct", collection_name))
        if pre_delete_collection:
            FakeVectorStore.collections[collection_name] = []

    def create_collection(self):
        FakeVectorStore.collections.setdefault(self.name, [])

    def add_texts(self, pieces):
        if FakeVectorStore.entered:
            FakeVectorStore.entered.set()
        if FakeVectorStore.hold:
            FakeVectorStore.hold.wait(5)
        if FakeVectorStore.fail:
            raise RuntimeError("embedding service unavailable")
        FakeVectorStore.collections[self.name].extend(pieces)

    @classmethod
    def reset(cls):
        cls.collections, cls.log = {}, []
        cls.hold = cls.entered = None
        cls.fail = False


def markers(chunks):
    return sorted(set(re.findall(r"MKR\d\d", " ".join(chunks))))


class FakeStoreMixin:
    def patch_store(self):
        FakeVectorStore.reset()
        for p in (mock.patch.object(cb, "PGVectorEncrypt", FakeVectorStore),
                  mock.patch.object(cb.util, "get_encryption_key", lambda: b"k" * 32),
                  mock.patch.object(cb.util, "get_db_sqlalchemy_url", lambda: "fake://"),
                  mock.patch.object(cb.util, "get_pgvector_engine_args", lambda: None),
                  mock.patch("builtins.print")):
            p.start()
            self.addCleanup(p.stop)


class InitBotGuardTests(FakeStoreMixin, SimpleTestCase):
    """Cases A and C at the initBot seam."""

    def setUp(self):
        self.patch_store()

    def test_stale_guard_touches_nothing(self):                       # A
        FakeVectorStore.collections["collection_s"] = ["MKR11 document B"]
        result = cb.initBot(testimony(1), "s", still_current=lambda: False)
        self.assertIsNone(result)
        self.assertEqual(FakeVectorStore.log, [])
        self.assertEqual(FakeVectorStore.collections["collection_s"], ["MKR11 document B"])

    def test_guard_runs_inside_db_lock(self):
        seen = []
        cb.initBot(testimony(1), "s", still_current=lambda: seen.append(cb.db_lock.locked()) or True)
        self.assertEqual(seen, [True])

    def test_job_indexing_before_replacement_is_overwritten_by_the_new_job(self):  # C
        current = {"job": "A"}
        FakeVectorStore.hold, FakeVectorStore.entered = threading.Event(), threading.Event()
        a = threading.Thread(target=cb.initBot, args=(testimony(1), "s"),
                             kwargs={"still_current": lambda: current["job"] == "A"})
        a.start()
        self.assertTrue(FakeVectorStore.entered.wait(5))   # A holds db_lock mid-write
        current["job"] = "B"                                # B becomes current
        FakeVectorStore.entered = None
        b = threading.Thread(target=cb.initBot, args=(testimony(11), "s"),
                             kwargs={"still_current": lambda: current["job"] == "B"})
        b.start()                                           # waits on db_lock
        FakeVectorStore.hold.set()
        a.join(5)
        b.join(5)
        self.assertEqual(markers(FakeVectorStore.collections["collection_s"]), ["MKR11"])


class IndexRaceTests(FakeStoreMixin, JobIsolationBase):
    """Cases B, D, E and the session markers, through the real views and create_summary."""

    def setUp(self):
        super().setUp()
        self.patch_store()
        # JobIsolationBase stubs initBot; use the real one on the fake store
        p = mock.patch.object(cb, "initBot", REAL_INIT_BOT)
        p.start()
        self.addCleanup(p.stop)
        self.ask = mock.patch.object(views, "askQuestion", return_value=("answer", [])).start()
        self.addCleanup(mock.patch.stopall)

    def collection(self):
        return FakeVectorStore.collections.get(f"collection_{self.client.session.session_key}", [])

    def post_question(self):
        return self.client.post("/ask", {"question": "What happened?"})

    def test_stale_job_cannot_replace_the_new_jobs_index(self):       # B
        real_init = REAL_INIT_BOT
        calls = []

        def init_with_pause(text, sid, **kwargs):
            if not calls:                      # job A pauses before db_lock ...
                calls.append("A")
                self.client.post("/clear")     # ... user cancels and uploads B,
                self.upload(DOC_B)
                self.threads.run(1)            # B finishes, index = B
                self.state_b = dict(self.client.session.items())
                self.index_b = list(self.collection())
            return real_init(text, sid, **kwargs)

        self.upload(DOC_A)
        with mock.patch.object(cb, "initBot", init_with_pause):
            self.threads.run(0)                # A wakes and reaches initBot

        self.assertEqual(markers(self.collection()), ["MKR11", "MKR12"])
        self.assertEqual(self.collection(), self.index_b)
        self.assertEqual(dict(self.client.session.items()), self.state_b)
        session = self.client.session
        self.assertEqual(session["chat_job_id"], session["job_id"])
        self.assertEqual(self.post_question().status_code, 200)

    def test_failed_index_disables_chat_instead_of_using_a_stale_one(self):  # D
        self.upload(DOC_A)
        self.threads.run(0)
        self.assertEqual(markers(self.collection()), ["MKR01", "MKR02", "MKR03"])

        self.upload(DOC_B)                    # new upload after A finished
        FakeVectorStore.fail = True
        self.threads.run(1)
        session = self.client.session
        self.assertEqual(views.job_status(session), ("ready", None))   # summary still completes
        self.assertNotIn("chat_job_id", session)
        response = self.post_question()
        self.assertEqual(response.status_code, 409)
        self.assertIn("isn't available", response.content.decode())
        self.ask.assert_not_called()

    def test_successful_index_enables_chat(self):                        # E
        self.upload(DOC_B)
        self.threads.run(0)
        session = self.client.session
        self.assertEqual(session["chat_job_id"], session["job_id"])
        self.assertEqual(markers(self.collection()), ["MKR11", "MKR12"])
        response = self.post_question()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content.decode(), "answer")
        self.ask.assert_called_once()

    def test_new_upload_clears_previous_chat_marker(self):               # F
        self.upload(DOC_A)
        self.threads.run(0)
        self.upload(DOC_B)
        self.assertNotIn("chat_job_id", self.client.session)

    def test_clear_removes_chat_marker(self):                            # G
        self.upload(DOC_A)
        self.threads.run(0)
        self.client.post("/clear")
        self.assertNotIn("chat_job_id", self.client.session)

    def test_sign_in_cancellation_leaves_no_chat_marker(self):           # H
        self.upload(DOC_A)

        def fake_login(request, user):
            request.session.cycle_key()
        with mock.patch.object(views, "authenticate", return_value=object()), \
                mock.patch.object(views, "login", side_effect=fake_login):
            self.client.post("/auth", {"username": "synthetic", "password": "synthetic"})
        self.threads.run(0)                    # old worker wakes: refused everywhere
        self.assertNotIn("chat_job_id", self.client.session)
        self.assertNotIn("job_id", self.client.session)
        self.assertEqual(self.post_question().status_code, 409)

    def test_tokens_are_not_exposed(self):                               # I
        self.upload(DOC_A)
        self.threads.run(0)
        session = self.client.session
        tokens = {session["job_id"], session["chat_job_id"]}
        verify = self.client.get("/out/verify")
        pages = [verify.content.decode(), str(verify.headers)]
        pages += [self.client.get(p).content.decode() for p in ("/home", "/output", "/chat")]
        for token in tokens:
            for page in pages:
                self.assertNotIn(token, page)

    def test_legacy_summary_without_tokens_keeps_chat(self):
        session = self.client.session
        session.update({"db_len": 3, "summary_pdf": "x", "prompt_append": []})
        session.save()
        self.assertEqual(self.post_question().status_code, 200)
        self.ask.assert_called_once()
