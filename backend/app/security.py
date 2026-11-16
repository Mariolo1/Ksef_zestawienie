"""Hasła (bcrypt), sesja (JWT w ciasteczku httpOnly), szyfrowanie sekretów (Fernet)."""
import datetime as dt

import bcrypt
import jwt
from cryptography.fernet import Fernet
from fastapi import Cookie, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from . import config
from .db import get_db
from .models import User

COOKIE = "ksef_session"
_fernet = Fernet(config.MASTER_KEY.encode())


# --- hasła ---
def hash_password(pw: str) -> str:
    return bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode()


def verify_password(pw: str, hashed: str | None) -> bool:
    if not hashed:
        return False
    return bcrypt.checkpw(pw.encode(), hashed.encode())


# --- sesja ---
def set_session(resp: Response, user: User):
    exp = dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=config.SESSION_HOURS)
    token = jwt.encode({"sub": str(user.id), "exp": exp}, config.SECRET_KEY, algorithm="HS256")
    resp.set_cookie(COOKIE, token, httponly=True, samesite="lax", secure=config.COOKIE_SECURE,
                    max_age=config.SESSION_HOURS * 3600, path="/")


def clear_session(resp: Response):
    resp.delete_cookie(COOKIE, path="/")


def current_user(ksef_session: str | None = Cookie(default=None), db: Session = Depends(get_db)) -> User:
    if not ksef_session:
        raise HTTPException(401, "Nie zalogowano")
    try:
        data = jwt.decode(ksef_session, config.SECRET_KEY, algorithms=["HS256"])
    except jwt.PyJWTError:
        raise HTTPException(401, "Sesja wygasła - zaloguj się ponownie")
    user = db.get(User, int(data["sub"]))
    if not user:
        raise HTTPException(401, "Nie zalogowano")
    return user


# --- szyfrowanie sekretów w bazie ---
def encrypt(b: bytes) -> bytes:
    return _fernet.encrypt(b)


def decrypt(b: bytes) -> bytes:
    return _fernet.decrypt(b)
