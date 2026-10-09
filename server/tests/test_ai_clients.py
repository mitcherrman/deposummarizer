"""
BEAR-V2-B4 AI client construction: lazy (nothing at import, no OPENAI_KEY
needed to import or run checks), centralized model / temperature /
structured-output configuration, and the exact request the real ChatOpenAI
sends, captured by an in-process httpx transport (no network).
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import httpx
from django.test import SimpleTestCase, override_settings

from server.summary import ai_clients, schema, summarizer
from server.summary import deposition_chatbot as cb
from server.tests.b4_fixtures import NoNetworkMixin

REPO = Path(__file__).resolve().parent.parent.parent


def completion(content: str) -> dict:
    return {
        "id": "chatcmpl-test", "object": "chat.completion", "created": 0, "model": "m",
        "choices": [{"index": 0, "finish_reason": "stop",
                     "message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }


class CapturingTransport:
    """httpx transport that records each request body and answers locally."""

    def __init__(self, content):
        self.bodies, self.content = [], content

    def __call__(self, request: httpx.Request):
        self.bodies.append(json.loads(request.content))
        return httpx.Response(200, json=completion(self.content))

    def client(self):
        return httpx.Client(transport=httpx.MockTransport(self))


class ClientTestBase(NoNetworkMixin, SimpleTestCase):
    def setUp(self):
        self.block_network()
        ai_clients.reset_clients()
        self.addCleanup(ai_clients.reset_clients)

    def request_for(self, model, **settings_overrides):
        transport = CapturingTransport(json.dumps({"status": "summarized", "bullets": ["Ok."], "uncertain": False}))
        with override_settings(SUMMARY_MODEL=model, **settings_overrides):
            client = ai_clients.summary_client(http_client=transport.client())
            reply = client.invoke([{"role": "system", "content": "s"}, {"role": "user", "content": "{}"}])
        self.assertEqual(len(transport.bodies), 1)
        return transport.bodies[0], client, reply


class LazyInitializationTests(ClientTestBase):
    def run_python(self, code, **env_overrides):
        env = {k: v for k, v in os.environ.items() if k not in ("OPENAI_KEY", "GPT_MODEL", "DJANGO_SETTINGS_MODULE")}
        env.update(DEBUG_MODE="True", USE_LOCAL_DB="True", STATIC_ROOT=str(REPO / ".test-static"),
                   PYTHONIOENCODING="utf-8", **env_overrides)
        return subprocess.run([sys.executable, "-W", "ignore", "-c", code], cwd=REPO, env=env,
                              capture_output=True, text=True, encoding="utf-8", timeout=120)

    def test_importing_the_app_needs_no_key_and_builds_no_client(self):
        code = (
            "import os, sys, django\n"
            "os.environ['DJANGO_SETTINGS_MODULE'] = 'server.settings'\n"
            "django.setup()\n"
            "import server.urls, server.views\n"
            "from server.summary import summarizer, deposition_chatbot as cb, ai_clients\n"
            "assert 'OPENAI_KEY' not in os.environ\n"
            "assert summarizer.llm is None and summarizer.translator_llm is None\n"
            "assert cb.model is None and cb.embedding is None\n"
            "import server.vector_db_session as engine\n"
            "assert engine.embedding is None\n"
            "assert ai_clients._clients == {}\n"
            "assert 'langchain_openai' not in sys.modules, 'langchain_openai imported at import time'\n"
            "print('ok')\n"
        )
        result = self.run_python(code)
        self.assertEqual(result.returncode, 0, result.stderr)
        # (pdf2docx prints PyMuPDF's own fitz deprecation notice on import)
        self.assertEqual(result.stdout.strip().splitlines()[-1], "ok")

    def test_manage_py_check_runs_without_an_openai_key(self):
        env = {k: v for k, v in os.environ.items() if k not in ("OPENAI_KEY", "GPT_MODEL", "DJANGO_SETTINGS_MODULE")}
        env.update(DEBUG_MODE="True", USE_LOCAL_DB="True", STATIC_ROOT=str(REPO / ".test-static"),
                   PYTHONIOENCODING="utf-8")
        result = subprocess.run([sys.executable, "-W", "ignore", "manage.py", "check"], cwd=REPO, env=env,
                                capture_output=True, text=True, encoding="utf-8", timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("no issues", result.stdout)

    def test_clients_are_built_once_on_first_use(self):
        first = ai_clients.summary_client()
        self.assertIs(ai_clients.summary_client(), first)
        self.assertIs(ai_clients.translation_client(), ai_clients.translation_client())
        ai_clients.reset_clients()
        self.assertIsNot(ai_clients.summary_client(), first)

    def test_summarizer_hooks_take_precedence(self):
        fake = object()
        summarizer.llm = fake
        try:
            self.assertIs(summarizer._summary_llm("v2"), fake)
            self.assertIs(summarizer._summary_llm("v1"), fake)
        finally:
            summarizer.llm = None
        self.assertIsInstance(summarizer._summary_llm("v2"), ai_clients.StructuredChatClient)
        self.assertNotIsInstance(summarizer._summary_llm("v1"), ai_clients.StructuredChatClient)

    def test_chatbot_clients_keep_their_configuration(self):
        model = cb._model()
        self.assertEqual(model.model_name, os.environ["GPT_MODEL"])
        self.assertEqual(model.temperature, 1)
        self.assertEqual(cb._embedding().model, "text-embedding-3-small")
        self.assertIs(cb._model(), model)


class RequestShapeTests(ClientTestBase):
    def test_structured_summary_request(self):
        body, client, reply = self.request_for("gpt-4o-mini")
        self.assertEqual(body["model"], "gpt-4o-mini")
        self.assertEqual(body["temperature"], 0)
        self.assertEqual(body["response_format"], {
            "type": "json_schema",
            "json_schema": {"name": "deposition_page_summary", "strict": True, "schema": schema.SUMMARY_JSON_SCHEMA},
        })
        self.assertEqual(client.chat_model.max_retries, 0)        # the pipeline owns retries
        self.assertEqual(client.chat_model.request_timeout, 120)
        # the raw JSON text comes back for the pipeline's own validation
        self.assertEqual(schema.parse_summary_response(summarizer._content_text(reply)).bullets, ("Ok.",))

    def test_translation_request_uses_its_own_schema(self):
        transport = CapturingTransport(json.dumps({"bullets": ["Uno."]}))
        client = ai_clients.translation_client(http_client=transport.client())
        client.invoke([{"role": "user", "content": "{}"}])
        fmt = transport.bodies[0]["response_format"]
        self.assertEqual(fmt["json_schema"]["name"], "spanish_translation")
        self.assertEqual(fmt["json_schema"]["schema"], schema.TRANSLATION_JSON_SCHEMA)

    def test_temperature_is_omitted_where_unsupported(self):
        for model in ("o3-mini", "o4-mini", "gpt-5-mini", "gpt-5.1"):
            with self.subTest(model=model):
                body, _, _ = self.request_for(model)
                self.assertNotIn("temperature", body)
        body, _, _ = self.request_for("gpt-5-chat-latest")
        self.assertEqual(body["temperature"], 0)

    def test_temperature_setting(self):
        body, _, _ = self.request_for("gpt-4o-mini", SUMMARY_TEMPERATURE=0.2)
        self.assertEqual(body["temperature"], 0.2)
        body, _, _ = self.request_for("gpt-4o-mini", SUMMARY_TEMPERATURE=None)
        self.assertNotIn("temperature", body)

    def test_json_mode_for_models_without_structured_outputs(self):
        for model in ("gpt-3.5-turbo", "gpt-4-turbo", "gpt-4"):
            with self.subTest(model=model):
                body, _, _ = self.request_for(model)
                self.assertEqual(body["response_format"], {"type": "json_object"})
        body, _, _ = self.request_for("gpt-4o-mini", SUMMARY_RESPONSE_FORMAT="json_object")
        self.assertEqual(body["response_format"], {"type": "json_object"})
        # json_object mode requires the word JSON in the messages: the prompts say it
        from server.summary import prompts
        self.assertIn("JSON", prompts.SUMMARY_SYSTEM_PROMPT)
        self.assertIn("JSON", prompts.TRANSLATION_SYSTEM_PROMPT)


class ModelConfigurationTests(ClientTestBase):
    def test_summary_model_comes_from_configuration(self):
        with override_settings(SUMMARY_MODEL="configured-model"):
            self.assertEqual(ai_clients.summary_client().chat_model.model_name, "configured-model")

    def test_translation_model_defaults_to_the_summary_model(self):
        with override_settings(SUMMARY_MODEL="configured-model", TRANSLATION_MODEL=""):
            self.assertEqual(ai_clients.translation_client().chat_model.model_name, "configured-model")
        with override_settings(TRANSLATION_MODEL="other-model"):
            self.assertEqual(ai_clients.translation_client().chat_model.model_name, "other-model")

    def test_legacy_translator_only_on_the_v1_path(self):
        self.assertEqual(ai_clients.legacy_translator_llm().model_name, ai_clients.LEGACY_TRANSLATION_MODEL)
        self.assertEqual(ai_clients.legacy_summary_llm().temperature, 1)
        self.assertNotEqual(ai_clients.translation_client().chat_model.model_name,
                            ai_clients.LEGACY_TRANSLATION_MODEL)

    def test_settings_parsing(self):
        from server.settings import _optional_float
        self.assertEqual(_optional_float("0"), 0.0)
        self.assertEqual(_optional_float("0.3"), 0.3)
        for value in ("", "none", "Default"):
            self.assertIsNone(_optional_float(value))

    def test_pipeline_defaults(self):
        from django.conf import settings
        self.assertEqual(settings.SUMMARY_PIPELINE_VERSION, "v2")
        self.assertFalse(settings.SUMMARY_NEIGHBOR_CONTEXT)
        self.assertEqual(settings.SUMMARY_TEMPERATURE, 0.0)
