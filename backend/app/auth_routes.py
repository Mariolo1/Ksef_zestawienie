"""Logowanie: e-mail + hasło oraz Google (OpenID Connect)."""
import re

from authlib.integrations.starlette_client import OAuth, OAuthError
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import config
from .db import get_db
from .models import User
from .security import clear_session, current_user, hash_password, set_session, verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

oauth = OAuth()
if config.GOOGLE_ENABLED:
    oauth.register(
        name="google",
        client_id=config.GOOGLE_CLIENT_ID,
        client_secret=config.GOOGLE_CLIENT_SECRET,
        server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
        client_kwargs={"scope": "openid email profile"},
    )


class Credentials(BaseModel):
    email: str
    password: str


def user_out(u: User):
    return {"id": u.id, "email": u.email, "has_password": bool(u.password_hash),
            "google": bool(u.google_sub)}


@router.get("/config")
def auth_config():
    return {"google": config.GOOGLE_ENABLED}


@router.post("/register")
def register(c: Credentials, resp: Response, db: Session = Depends(get_db)):
    email = c.email.strip().lower()
    if not EMAIL_RE.match(email):
        raise HTTPException(400, "Nieprawidłowy adres e-mail")
    if len(c.password) < 10:
        raise HTTPException(400, "Hasło musi mieć co najmniej 10 znaków")
    if len(c.password.encode()) > 72:
        raise HTTPException(400, "Hasło może mieć najwyżej 72 bajty")
    if db.scalar(select(User).where(User.email == email)):
        raise HTTPException(409, "Konto z tym adresem już istnieje")
    u = User(email=email, password_hash=hash_password(c.password))
    db.add(u)
    db.commit()
    set_session(resp, u)
    return user_out(u)


@router.post("/login")
def login(c: Credentials, resp: Response, db: Session = Depends(get_db)):
    u = db.scalar(select(User).where(User.email == c.email.strip().lower()))
    if not u or not verify_password(c.password, u.password_hash):
        raise HTTPException(401, "Błędny e-mail lub hasło")
    set_session(resp, u)
    return user_out(u)


@router.post("/logout")
def logout(resp: Response):
    clear_session(resp)
    return {"ok": True}


@router.get("/me")
def me(u: User = Depends(current_user)):
    return user_out(u)


# --- Google ---
@router.get("/google/login")
async def google_login(request: Request):
    if not config.GOOGLE_ENABLED:
        raise HTTPException(404, "Logowanie Google nie jest skonfigurowane")
    return await oauth.google.authorize_redirect(request, f"{config.PUBLIC_URL}/api/auth/google/callback")


@router.get("/google/callback")
async def google_callback(request: Request, db: Session = Depends(get_db)):
    if not config.GOOGLE_ENABLED:
        raise HTTPException(404)
    try:
        token = await oauth.google.authorize_access_token(request)
    except OAuthError:
        return RedirectResponse(f"{config.PUBLIC_URL}/login?error=google")
    info = token.get("userinfo") or {}
    sub, email = info.get("sub"), (info.get("email") or "").lower()
    if not sub or not email or not info.get("email_verified"):
        return RedirectResponse(f"{config.PUBLIC_URL}/login?error=google_email")

    u = db.scalar(select(User).where(User.google_sub == sub))
    if not u:
        u = db.scalar(select(User).where(User.email == email))
        if u:  # istniejące konto z tym samym zweryfikowanym e-mailem - podpinamy Google
            u.google_sub = sub
        else:
            u = User(email=email, google_sub=sub)
            db.add(u)
        db.commit()
    resp = RedirectResponse(f"{config.PUBLIC_URL}/")
    set_session(resp, u)
    return resp
