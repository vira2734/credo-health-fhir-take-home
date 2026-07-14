"""Django settings for the Credo Health take-home backend."""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# This fallback is intentionally development-only. Production must provide a secret.
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "unsafe-development-key")
DEBUG = os.environ.get("DJANGO_DEBUG", "true").lower() in {"1", "true", "yes"}
ALLOWED_HOSTS: list[str] = []

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "fhir_migration.apps.FhirMigrationConfig",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

FHIR_BASE_URL = os.environ.get("FHIR_BASE_URL", "https://hapi.fhir.org/baseR4")
FHIR_DEFAULT_PATIENT_LIMIT = int(os.environ.get("FHIR_DEFAULT_PATIENT_LIMIT", "10"))
FHIR_PATIENT_PAGE_SIZE = int(os.environ.get("FHIR_PATIENT_PAGE_SIZE", "100"))
FHIR_OBSERVATION_PAGE_SIZE = int(os.environ.get("FHIR_OBSERVATION_PAGE_SIZE", "100"))
FHIR_CONNECT_TIMEOUT = float(os.environ.get("FHIR_CONNECT_TIMEOUT", "3.05"))
FHIR_READ_TIMEOUT = float(os.environ.get("FHIR_READ_TIMEOUT", "20"))
FHIR_MAX_RETRIES = int(os.environ.get("FHIR_MAX_RETRIES", "2"))
FHIR_BACKOFF_FACTOR = float(os.environ.get("FHIR_BACKOFF_FACTOR", "0.5"))
FHIR_MAX_BACKOFF = float(os.environ.get("FHIR_MAX_BACKOFF", "30"))
