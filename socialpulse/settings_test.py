"""
Test settings — runs the suite against a local in-memory SQLite database so
tests are fast and NEVER touch the production Supabase database.

Run with:
    python manage.py test --settings=socialpulse.settings_test
"""
from .settings import *  # noqa: F401,F403

# In-memory SQLite — no CREATE/DROP DATABASE against Supabase's pooler.
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

# Hard guardrail: refuse to run if anything points the default DB at Postgres.
assert DATABASES["default"]["ENGINE"] == "django.db.backends.sqlite3", (
    "Test settings must use SQLite — refusing to run against a real database."
)

# Local in-memory cache instead of Redis.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
    }
}

# Fast password hashing for tests (do NOT use in production).
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

# Capture emails in memory instead of sending them.
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

# Keep tests deterministic / offline.
DEBUG = False
VIRTUALNUMBER_PROFIT_MARGIN = 0.65

# The test client speaks plain HTTP; don't force HTTPS redirects in tests.
SECURE_SSL_REDIRECT = False
