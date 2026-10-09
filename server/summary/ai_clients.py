"""
ai_clients.py – lazily built OpenAI clients and the summary model configuration

Nothing in this module talks to OpenAI, reads the API key or imports
langchain_openai at import time. A client is built the first time a job (or
the chatbot) actually needs it and then reused. All model choices come from
settings (environment-driven); see server/settings.py "Summary pipeline".
"""
import re
from threading import Lock

from decouple import config
from django.conf import settings

from server.summary import schema

# v1 rollback path only: the translator model the pre-B4 pipeline hard-coded
LEGACY_TRANSLATION_MODEL = "gpt-3.5-turbo-0125"
# chatbot embeddings (unchanged)
EMBEDDING_MODEL = "text-embedding-3-small"

_clients = {}
_clients_lock = Lock()


def _cached(key, build):
    with _clients_lock:
        if key not in _clients:
            _clients[key] = build()
        return _clients[key]


def reset_clients():
    """Forget every built client (tests; configuration changes)."""
    with _clients_lock:
        _clients.clear()


# ─────────────────── Configuration ───────────────────────────────────────
def summary_model() -> str:
    return settings.SUMMARY_MODEL


def translation_model() -> str:
    return settings.TRANSLATION_MODEL or summary_model()


def temperature_supported(model: str) -> bool:
    """
    False for model families that reject a non-default temperature: the
    o-series reasoning models and gpt-5 models other than the *-chat variants
    (langchain-openai itself drops temperature for the latter).
    """
    m = model.lower()
    if re.match(r"o\d", m):
        return False
    if m.startswith("gpt-5") and "chat" not in m:
        return False
    return True


def temperature_for(model: str):
    """The configured temperature, or None (= don't send it) where unsupported."""
    value = settings.SUMMARY_TEMPERATURE
    if value is None or not temperature_supported(model):
        return None
    return value


def structured_output_mode(model: str) -> str:
    """
    "json_schema" (strict Structured Outputs) or "json_object" (JSON mode).
    auto: json_schema except for the models the installed langchain-openai
    lists as lacking Structured Outputs (gpt-3*, gpt-4-*, gpt-4).
    """
    mode = settings.SUMMARY_RESPONSE_FORMAT
    if mode in ("json_schema", "json_object"):
        return mode
    m = model.lower()
    if m.startswith("gpt-3") or m.startswith("gpt-4-") or m == "gpt-4":
        return "json_object"
    return "json_schema"


def response_format(name: str, json_schema: dict, model: str) -> dict:
    """The OpenAI chat-completions response_format value for this model."""
    if structured_output_mode(model) == "json_object":
        return {"type": "json_object"}
    return {"type": "json_schema",
            "json_schema": {"name": name, "strict": True, "schema": json_schema}}


# ─────────────────── Client construction ─────────────────────────────────
def _api_key() -> str:
    return config("OPENAI_KEY").strip()


def build_chat_model(model: str, temperature=None, api_key=None, **extra):
    """A ChatOpenAI; temperature is sent only when not None. api_key defaults to OPENAI_KEY."""
    from langchain_openai import ChatOpenAI   # deferred: importing it is not free
    kwargs = dict(openai_api_key=api_key or _api_key(), model_name=model, **extra)
    if temperature is not None:
        kwargs["temperature"] = temperature
    return ChatOpenAI(**kwargs)


class StructuredChatClient:
    """
    A chat model invoked with a fixed response_format. invoke(messages)
    returns the provider message; its .content is the raw JSON text, which
    the pipeline validates itself (summary.schema).
    """

    def __init__(self, chat_model, response_format_value: dict):
        self.chat_model = chat_model
        self.response_format = response_format_value

    def invoke(self, messages):
        return self.chat_model.invoke(messages, response_format=self.response_format)


def _structured_client(role, json_schema, model, http_client=None, api_key=None):
    # max_retries=0: the pipeline owns retries (bounded, cancellable, visible
    # in the job status), so the SDK must not multiply them
    extra = {"max_retries": 0, "timeout": settings.SUMMARY_REQUEST_TIMEOUT}
    if http_client is not None:
        extra["http_client"] = http_client
    chat = build_chat_model(model, temperature_for(model), api_key=api_key, **extra)
    return StructuredChatClient(chat, response_format(role, json_schema, model))


def summary_client(http_client=None):
    """v2 page-summary client (structured output, low temperature)."""
    model = summary_model()
    if http_client is not None:      # tests: never cached
        return _structured_client("deposition_page_summary", schema.SUMMARY_JSON_SCHEMA, model, http_client)
    key = ("summary", model, temperature_for(model), structured_output_mode(model),
           settings.SUMMARY_REQUEST_TIMEOUT)
    return _cached(key, lambda: _structured_client(
        "deposition_page_summary", schema.SUMMARY_JSON_SCHEMA, model))


def translation_client(http_client=None):
    """v2 bullet-array translation client."""
    model = translation_model()
    if http_client is not None:
        return _structured_client("spanish_translation", schema.TRANSLATION_JSON_SCHEMA, model, http_client)
    key = ("translation", model, temperature_for(model), structured_output_mode(model),
           settings.SUMMARY_REQUEST_TIMEOUT)
    return _cached(key, lambda: _structured_client(
        "spanish_translation", schema.TRANSLATION_JSON_SCHEMA, model))


def evaluation_clients(model: str, api_key: str):
    """Uncached (summary, translation) clients for the evaluation harness's live mode."""
    return (_structured_client("deposition_page_summary", schema.SUMMARY_JSON_SCHEMA, model, api_key=api_key),
            _structured_client("spanish_translation", schema.TRANSLATION_JSON_SCHEMA, model, api_key=api_key))


def legacy_summary_llm():
    """v1 rollback path: the pre-B4 summary client (temperature 1)."""
    model = summary_model()
    return _cached(("legacy-summary", model), lambda: build_chat_model(model, 1))


def legacy_translator_llm():
    """v1 rollback path: the pre-B4 translator."""
    return _cached(("legacy-translator",),
                   lambda: build_chat_model(LEGACY_TRANSLATION_MODEL))


def chatbot_model():
    """Chatbot answer model, configured exactly as before B4 (GPT_MODEL required, temperature 1)."""
    def build():
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(openai_api_key=config('OPENAI_KEY'), model_name=config('GPT_MODEL'), temperature=1)
    return _cached(("chatbot",), build)


def chatbot_embeddings():
    """Chatbot embeddings, unchanged model."""
    def build():
        from langchain_openai import OpenAIEmbeddings
        return OpenAIEmbeddings(model=EMBEDDING_MODEL, api_key=config('OPENAI_KEY'))
    return _cached(("embeddings",), build)
