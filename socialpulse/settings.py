import os
from pathlib import Path
from datetime import timedelta
from dotenv import load_dotenv
import dj_database_url


load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

VIRTUALNUMBER_PROFIT_MARGIN = 5.0 



SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "django-insecure-default-key")
DEBUG = os.getenv("DEBUG", "False") == "True"
ALLOWED_HOSTS = os.getenv("ALLOWED_HOSTS", "*").split(",")


INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "rest_framework_simplejwt",
    "corsheaders",
    "users",
    "payments",
    "support",
    "boost",
    "virtualnumbers",
]

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",  
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]


ROOT_URLCONF = "socialpulse.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
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

WSGI_APPLICATION = "socialpulse.wsgi.application"

if os.getenv("DATABASE_URL"):
    DATABASES = {
        "default": dj_database_url.parse(
            os.getenv("DATABASE_URL"),
            conn_max_age=600,
            ssl_require=True
        )
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "HOST": os.getenv("DB_HOST"),
            "PORT": os.getenv("DB_PORT", 6543),
            "USER": os.getenv("DB_USER"),
            "PASSWORD": os.getenv("DB_PASSWORD"),
            "NAME": os.getenv("DB_NAME"),
            "OPTIONS": {"sslmode": "require"},
        }
    }


AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_STORAGE = "whitenoise.storage.CompressedManifestStaticFilesStorage"

CORS_ALLOWED_ORIGINS = [
    "https://socialpulse-cstr.vercel.app",
    "http://localhost:5173",          
    "http://127.0.0.1:5173", 
]

CSRF_TRUSTED_ORIGINS = [
    "https://socialpulse-cstr.vercel.app",
    "https://socialpulsebackend.onrender.com",  
    "http://localhost:8000",
    "http://127.0.0.1:8000",
]


CORS_ALLOW_CREDENTIALS = True


REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ),
    "DEFAULT_PERMISSION_CLASSES": (
        "rest_framework.permissions.IsAuthenticated",
    ),
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(days=1),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=7),
    "AUTH_HEADER_TYPES": ("Bearer",),
}


AUTH_USER_MODEL = "users.User"

PAYSTACK_SECRET_KEY = os.getenv("PAYSTACK_SECRET_KEY")
PAYSTACK_PUBLIC_KEY = os.getenv("PAYSTACK_PUBLIC_KEY")
BACKEND_URL = os.getenv("BACKEND_URL")
FRONTEND_URL = os.getenv("FRONTEND_URL")
SMM_API_KEY = os.getenv("SMM_API_KEY")
SMM_API_URL = os.getenv("SMM_API_URL")
SMS_ACTIVATE_API_KEY = os.getenv("SMS_ACTIVATE_API_KEY")
EXCHANGE_RATE_API_KEY = os.getenv("EXCHANGE_RATE_API_KEY")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
