"""Dane firmy (NIP, środowisko KSeF) i certyfikat KSeF."""
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel
from sqlalchemy.orm import Session

from . import ksef_core
from .db import get_db
from .ksef_service import CertError, cert_valid_to, client_for, has_credentials, load_cert_key, valid_nip
from .models import Company, User
from .security import current_user, encrypt

router = APIRouter(prefix="/api/company", tags=["company"])

MAX_FILE = 64 * 1024  # certyfikat/klucz to kilka kB


class CompanyIn(BaseModel):
    nip: str
    name: str = ""
    ksef_env: str = "prod"


def company_out(c: Company | None):
    if not c:
        return None
    return {"nip": c.nip, "name": c.name, "ksef_env": c.ksef_env,
            "auth_method": c.auth_method, "has_token": bool(c.token_enc),
            "has_credentials": has_credentials(c),
            "has_certificate": bool(c.cert_enc), "cert_subject": c.cert_subject,
            "cert_valid_to": c.cert_valid_to.isoformat() if c.cert_valid_to else None}


@router.get("")
def get_company(u: User = Depends(current_user)):
    return company_out(u.company)


@router.put("")
def put_company(data: CompanyIn, u: User = Depends(current_user), db: Session = Depends(get_db)):
    nip = data.nip.replace("-", "").replace(" ", "")
    if not valid_nip(nip):
        raise HTTPException(400, "Nieprawidłowy NIP (10 cyfr, poprawna suma kontrolna)")
    if data.ksef_env not in ksef_core.API_URLS:
        raise HTTPException(400, "Środowisko KSeF: prod, test lub demo")
    c = u.company
    if c is None:
        c = Company(user_id=u.id, nip=nip)
        db.add(c)
    elif c.nip != nip:
        # inny NIP = certyfikat i token poprzedniego NIP-u tracą sens
        c.cert_enc = c.key_enc = c.key_password_enc = c.token_enc = None
        c.cert_subject, c.cert_valid_to = "", None
    c.nip, c.name, c.ksef_env = nip, data.name.strip(), data.ksef_env
    db.commit()
    db.refresh(c)
    return company_out(c)


async def _read(f: UploadFile | None):
    if f is None:
        return None
    b = await f.read(MAX_FILE + 1)
    if len(b) > MAX_FILE:
        raise HTTPException(400, f"Plik {f.filename} jest za duży")
    return b or None


@router.post("/certificate")
async def upload_certificate(
    cert: UploadFile | None = File(None), key: UploadFile | None = File(None),
    p12: UploadFile | None = File(None), password: str = Form(""),
    u: User = Depends(current_user), db: Session = Depends(get_db),
):
    c = u.company
    if c is None:
        raise HTTPException(400, "Najpierw zapisz NIP firmy")
    cert_b, key_b, p12_b = await _read(cert), await _read(key), await _read(p12)
    try:
        x509cert, pkey = load_cert_key(cert_b, key_b, password or None, p12_b)
    except CertError as e:
        raise HTTPException(400, str(e))

    from cryptography.hazmat.primitives import serialization
    # przechowujemy zawsze PEM (cert) + PEM (klucz zaszyfrowany oryginalnym hasłem lub jawny)
    cert_pem = x509cert.public_bytes(serialization.Encoding.PEM)
    enc_alg = (serialization.BestAvailableEncryption(password.encode()) if password
               else serialization.NoEncryption())
    key_pem = pkey.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, enc_alg)

    c.cert_enc = encrypt(cert_pem)
    c.key_enc = encrypt(key_pem)
    c.key_password_enc = encrypt(password.encode()) if password else None
    c.cert_subject = x509cert.subject.rfc4514_string()[:500]
    c.cert_valid_to = cert_valid_to(x509cert)
    c.auth_method = "cert"
    db.commit()
    return company_out(c)


class TokenIn(BaseModel):
    token: str


@router.post("/token")
def save_token(data: TokenIn, u: User = Depends(current_user), db: Session = Depends(get_db)):
    c = u.company
    if c is None:
        raise HTTPException(400, "Najpierw zapisz NIP firmy")
    token = data.token.strip()
    if len(token) < 20 or any(ch.isspace() for ch in token):
        raise HTTPException(400, "To nie wygląda na token KSeF (wklej cały token, bez spacji)")
    c.token_enc = encrypt(token.encode())
    c.auth_method = "token"
    db.commit()
    return company_out(c)


@router.delete("/token")
def delete_token(u: User = Depends(current_user), db: Session = Depends(get_db)):
    c = u.company
    if c:
        c.token_enc = None
        if c.auth_method == "token" and c.cert_enc:
            c.auth_method = "cert"
        db.commit()
    return company_out(c)


class MethodIn(BaseModel):
    auth_method: str


@router.put("/auth-method")
def set_method(data: MethodIn, u: User = Depends(current_user), db: Session = Depends(get_db)):
    c = u.company
    if c is None or data.auth_method not in ("cert", "token"):
        raise HTTPException(400, "Nieprawidłowa metoda logowania")
    c.auth_method = data.auth_method
    db.commit()
    return company_out(c)


@router.delete("/certificate")
def delete_certificate(u: User = Depends(current_user), db: Session = Depends(get_db)):
    c = u.company
    if c:
        c.cert_enc = c.key_enc = c.key_password_enc = None
        c.cert_subject, c.cert_valid_to = "", None
        if c.auth_method == "cert" and c.token_enc:
            c.auth_method = "token"
        db.commit()
    return company_out(c)


@router.post("/test")
async def test_connection(u: User = Depends(current_user)):
    """Próba logowania do KSeF certyfikatem firmy."""
    if not u.company:
        raise HTTPException(400, "Najpierw zapisz NIP firmy")
    try:
        await run_in_threadpool(client_for, u.company)
    except CertError as e:
        raise HTTPException(400, str(e))
    except ksef_core.KsefError as e:
        raise HTTPException(502, f"KSeF ({u.company.ksef_env}) odrzucił logowanie: {e}")
    how = "tokenem" if u.company.auth_method == "token" else "certyfikatem"
    return {"ok": True, "message": f"Zalogowano do KSeF ({u.company.ksef_env}) {how} jako NIP {u.company.nip}"}
