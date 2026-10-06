"""
Settings for the regression test suite (`python manage.py test`).

Only placeholder values are used: tests need no OpenAI key, AWS profile or
Postgres. OpenAI clients are still constructed at import time, but with a
placeholder key and no network call; every test that would reach the LLM
patches it with a fake.
"""
import os
import tempfile

# Forced (not setdefault) so a developer's real environment is never used.
os.environ["DEBUG_MODE"] = "True"
os.environ["USE_LOCAL_DB"] = "True"
os.environ["STATIC_ROOT"] = os.path.join(tempfile.gettempdir(), "bearsummarizer-test-static")
os.environ["OPENAI_KEY"] = "test-placeholder-not-a-key"
os.environ["GPT_MODEL"] = "test-placeholder-model"

from server.settings import *  # noqa: E402,F401,F403

# Never touch the custom Postgres engine. The tests are SimpleTestCase, so no
# test database is created at all; this is a safety net.
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

# Real Django sessions without a database or the PGVector-managing engine.
SESSION_ENGINE = "django.contrib.sessions.backends.cache"
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
    }
}
