"""Warstwa między aplikacją a rdzeniem KSeF (ksef_core.py)."""
import datetime as dt
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat, pkcs12

from . import ksef_core as core
from .models import Company
from cryptography.fernet import InvalidToken

from .security import decrypt as _decrypt


class CertError(ValueError):
    pass


def decrypt(b: bytes) -> bytes:
    try:
        return _decrypt(b)
    except InvalidToken:
        raise CertError("Nie można odszyfrować zapisanych danych logowania (zmieniono MASTER_KEY?) - "
                        "wgraj ponownie certyfikat lub token")


def valid_nip(nip: str) -> bool:
    if len(nip) != 10 or not nip.isdigit():
        return False
    w = (6, 5, 7, 2, 3, 4, 5, 6, 7)
    return sum(int(nip[i]) * w[i] for i in range(9)) % 11 == int(nip[9])


def load_cert_key(cert_bytes: bytes | None, key_bytes: bytes | None,
                  password: str | None, p12_bytes: bytes | None = None):
    """Wczytuje i sprawdza certyfikat + klucz. Zwraca (cert, key)."""
    pwd = password.encode() if password else None
    try:
        if p12_bytes:
            key, cert, _ = pkcs12.load_key_and_certificates(p12_bytes, pwd)
            if cert is None or key is None:
                raise CertError("Plik .p12 nie zawiera certyfikatu lub klucza prywatnego")
        else:
            if not cert_bytes or not key_bytes:
                raise CertError("Wgraj plik certyfikatu (.crt) i klucza prywatnego (.key)")
            cert = (x509.load_pem_x509_certificate(cert_bytes) if b"-----BEGIN" in cert_bytes
                    else x509.load_der_x509_certificate(cert_bytes))
            key = serialization.load_pem_private_key(key_bytes, password=pwd)
    except CertError:
        raise
    except (ValueError, TypeError) as e:
        raise CertError(f"Nie można wczytać certyfikatu lub klucza (złe hasło lub format): {e}")

    spki = lambda k: k.public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
    if spki(cert.public_key()) != spki(key.public_key()):
        raise CertError("Klucz prywatny nie pasuje do certyfikatu")
    return cert, key


def cert_valid_to(cert) -> dt.datetime:
    return getattr(cert, "not_valid_after_utc", None) or cert.not_valid_after.replace(tzinfo=dt.timezone.utc)


def has_credentials(company: Company) -> bool:
    if company.auth_method == "token":
        return bool(company.token_enc)
    return bool(company.cert_enc and company.key_enc)


def client_for(company: Company) -> core.KsefClient:
    """Loguje się do KSeF (certyfikatem albo tokenem - wg auth_method) i zwraca klienta."""
    if company.auth_method == "token":
        if not company.token_enc:
            raise CertError("Brak tokena KSeF - wpisz go w ustawieniach firmy")
        c = core.KsefClient(company.nip, decrypt(company.token_enc).decode(), company.ksef_env)
        c.login()
        return c
    if not company.cert_enc or not company.key_enc:
        raise CertError("Brak certyfikatu KSeF - wgraj go w ustawieniach firmy")
    pwd = decrypt(company.key_password_enc).decode() if company.key_password_enc else None
    cert, key = load_cert_key(decrypt(company.cert_enc), decrypt(company.key_enc), pwd)
    if cert_valid_to(cert) < dt.datetime.now(dt.timezone.utc):
        raise CertError("Certyfikat KSeF wygasł")
    c = core.KsefClient(company.nip, None, company.ksef_env)
    c.login_cert(cert, key)
    return c


def run_report(company: Company, date_from: dt.date, date_to: dt.date, kind: str, date_type: str,
               xml_dir: Path, out_path: Path, progress):
    """Pobiera faktury i zapisuje xlsx. progress(tekst) - aktualizacja statusu."""
    progress(f"Logowanie do KSeF ({company.ksef_env})...")
    client = client_for(company)
    client.on_wait = lambda s: progress(
        f"Limit API KSeF - wznowienie ok. "
        f"{(dt.datetime.now() + dt.timedelta(seconds=s)):%H:%M:%S} ({s} s)")

    dtype = {"wystawienia": "Issue", "ksef": "PermanentStorage"}[date_type]
    subjects = {"zakup": ["Subject2"], "sprzedaz": ["Subject1"], "oba": ["Subject2", "Subject1"]}[kind]
    xml_dir.mkdir(parents=True, exist_ok=True)

    records, errors = [], []
    for st in subjects:
        label = "zakup" if st == "Subject2" else "sprzedaż"
        progress(f"Wyszukiwanie faktur ({label})...")
        meta = client.query_metadata(st, date_from, date_to, dtype)
        for i, m in enumerate(meta, 1):
            nr = m["ksefNumber"]
            progress(f"Pobieranie ({label}) {i}/{len(meta)}")
            f = xml_dir / f"{nr}.xml"
            try:
                if not f.exists():
                    f.write_bytes(client.download_xml(nr))
                rec = core.parse_fa(f.read_bytes())
                rec.update(ksef_nr=nr, plik=f.name, data_ksef=core.to_date(m.get("acquisitionDate", "")))
                records.append(rec)
            except Exception as e:  # pojedyncza faktura nie przerywa raportu
                errors.append((nr, str(e)))

    progress("Tworzenie pliku Excel...")
    core.write_xlsx(records, errors, company.nip, out_path, f"{date_from} - {date_to}")
    return len(records), len(errors)
