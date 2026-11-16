"""Konfiguracja z zmiennych środowiskowych (.env w docker compose)."""
import os
from pathlib import Path


def _req(name):
    v = os.environ.get(name)
    if not v:
        raise RuntimeError(f"Brak zmiennej środowiskowej {name} (patrz .env.example)")
    return v


DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./data/app.db")
DATA_DIR = Path(os.environ.get("DATA_DIR", "./data"))

# Sekret do podpisywania sesji (JWT w ciasteczku) i stanu OAuth
SECRET_KEY = _req("SECRET_KEY")
# Klucz Fernet do szyfrowania certyfikatów i haseł do kluczy w bazie
MASTER_KEY = _req("MASTER_KEY")

SESSION_HOURS = int(os.environ.get("SESSION_HOURS", "12"))
COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "false").lower() == "true"  # true przy HTTPS

# Adres, pod którym użytkownik otwiera aplikację (do przekierowań po logowaniu Google)
PUBLIC_URL = os.environ.get("PUBLIC_URL", "http://localhost:8080").rstrip("/")

GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
GOOGLE_ENABLED = bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET)

DATA_DIR.mkdir(parents=True, exist_ok=True)
