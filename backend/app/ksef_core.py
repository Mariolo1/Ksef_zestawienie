#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Rdzeń KSeF (kopia ksef_zestawienie.py) - używany przez backend.
ksef_zestawienie.py - zestawienie faktur z KSeF dla księgowej (Excel .xlsx)

Bez AI, bez OCR. Dane pochodzą wyłącznie ze struktury XML FA(3) (oficjalny
schemat MF: http://crd.gov.pl/wzor/2025/06/25/13775/). PDF z KSeF jest tylko
wizualizacją XML-a - skrypt z PDF odczytuje wyłącznie numer KSeF, a dane
pobiera z oryginalnego XML w KSeF.

Tryby:
  pobierz  - pobiera faktury z API KSeF 2.0 za okres i robi zestawienie
  pliki    - robi zestawienie z folderu z plikami .xml i/lub .pdf z KSeF
             (dla PDF: wyciąga numer KSeF i pobiera XML z API)
  serwuj   - udostępnia folder z raportami przez HTTP do pobrania

Konfiguracja (zmienne środowiskowe lub plik ksef.conf obok skryptu):
  KSEF_NIP=1234567890
  KSEF_ENV=prod            # prod | test | demo
  KSEF_AUTH=cert           # cert | token (domyślnie: cert, jeśli podano certyfikat)

  Logowanie tokenem (działa do 31.12.2026):
  KSEF_TOKEN=<token KSeF>

  Logowanie certyfikatem KSeF (XAdES) - pliki z MCU:
  KSEF_CERT=/sciezka/certyfikat.crt      # PEM lub DER
  KSEF_KEY=/sciezka/klucz.key            # klucz prywatny PEM
  KSEF_KEY_PASSWORD=haslo_do_klucza      # jeśli klucz jest zaszyfrowany
  albo zamiast CERT+KEY:
  KSEF_P12=/sciezka/certyfikat.p12  (+ KSEF_KEY_PASSWORD)

Wymagania: pip install requests cryptography openpyxl pypdf
           + dla certyfikatu: pip install signxml lxml
"""

import argparse
import base64
import datetime as dt
import functools
import http.server
import os
import re
import socketserver
import sys
import time
import xml.etree.ElementTree as ET
from decimal import Decimal, InvalidOperation
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

# --------------------------------------------------------------------------
# Stałe
# --------------------------------------------------------------------------
API_URLS = {
    "prod": "https://api.ksef.mf.gov.pl/v2",
    "test": "https://api-test.ksef.mf.gov.pl/v2",
    "demo": "https://api-demo.ksef.mf.gov.pl/v2",
}

# Format numeru KSeF wg OpenAPI KSeF 2.0: NIP-RRRRMMDD-XXXXXX-XXXXXX-XX (hex)
KSEF_NR_RE = re.compile(
    r"[1-9]\d{9}-20\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])"
    r"-[0-9A-F]{6}-?[0-9A-F]{6}-[0-9A-F]{2}"
)

FORMA_PLATNOSCI = {"1": "Gotówka", "2": "Karta", "3": "Bon", "4": "Czek",
                   "5": "Kredyt", "6": "Przelew", "7": "Mobilna"}

RODZAJ_FAKTURY = {"VAT": "Podstawowa", "KOR": "Korygująca", "ZAL": "Zaliczkowa",
                  "ROZ": "Rozliczeniowa", "UPR": "Uproszczona",
                  "KOR_ZAL": "Korekta zaliczkowej", "KOR_ROZ": "Korekta rozliczeniowej"}

# (pole netto, pole VAT, pole VAT w PLN przy walucie obcej, etykieta)
# wg schematu FA(3)
STAWKI = [
    ("P_13_1", "P_14_1", "P_14_1W", "23%/22%"),
    ("P_13_2", "P_14_2", "P_14_2W", "8%/7%"),
    ("P_13_3", "P_14_3", "P_14_3W", "5%"),
    ("P_13_4", "P_14_4", "P_14_4W", "Ryczałt taxi"),
    ("P_13_5", "P_14_5", None, "OSS (dz. XII rozdz. 6a)"),
    ("P_13_6_1", None, None, "0% kraj"),
    ("P_13_6_2", None, None, "0% WDT"),
    ("P_13_6_3", None, None, "0% eksport"),
    ("P_13_7", None, None, "Zwolnione"),
    ("P_13_8", None, None, "NP poza krajem"),
    ("P_13_9", None, None, "NP art.100"),
    ("P_13_10", None, None, "Odwrotne obciążenie"),
    ("P_13_11", None, None, "Marża"),
]

ZERO = Decimal("0")


# --------------------------------------------------------------------------
# Konfiguracja
# --------------------------------------------------------------------------
def load_config():
    cfg = {}
    conf_file = Path(__file__).with_name("ksef.conf")
    if conf_file.exists():
        for line in conf_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                cfg[k.strip()] = v.strip().strip('"').strip("'")
    for k in ("KSEF_NIP", "KSEF_TOKEN", "KSEF_ENV", "KSEF_AUTH", "KSEF_CERT", "KSEF_KEY",
              "KSEF_KEY_PASSWORD", "KSEF_P12"):
        if os.environ.get(k):
            cfg[k] = os.environ[k]
    cfg.setdefault("KSEF_ENV", "prod")
    cfg.setdefault("KSEF_AUTH", "cert" if (cfg.get("KSEF_CERT") or cfg.get("KSEF_P12")) else "token")
    if cfg["KSEF_AUTH"] not in ("cert", "token"):
        sys.exit("KSEF_AUTH musi być: cert albo token")
    if cfg["KSEF_ENV"] not in API_URLS:
        sys.exit(f"KSEF_ENV musi być jednym z: {', '.join(API_URLS)}")
    return cfg


# --------------------------------------------------------------------------
# Klient API KSeF 2.0 (uwierzytelnienie tokenem KSeF)
# --------------------------------------------------------------------------
class KsefError(Exception):
    pass


class KsefClient:
    def __init__(self, nip, token, env="prod", timeout=60):
        self.base = API_URLS[env]
        self.nip = nip
        self.token = token
        self.timeout = timeout
        self.s = requests.Session()
        self.s.headers["Accept-Encoding"] = "gzip"
        self.access_token = None
        self.on_wait = None  # callback(sekundy) przy limicie 429

    # -- HTTP z obsługą limitów (429 + Retry-After) --
    def _req(self, method, path, bearer=None, **kw):
        headers = kw.pop("headers", {})
        if bearer:
            headers["Authorization"] = f"Bearer {bearer}"
        for attempt in range(6):
            r = self.s.request(method, self.base + path, headers=headers,
                               timeout=self.timeout, **kw)
            if r.status_code == 429:
                wait = r.headers.get("Retry-After", "")
                wait = int(wait) if wait.isdigit() else 2 ** attempt
                if self.on_wait:
                    self.on_wait(wait)
                else:
                    print(f"  limit API (429), czekam {wait}s...", file=sys.stderr)
                time.sleep(wait)
                continue
            if r.status_code >= 400:
                raise KsefError(f"{method} {path} -> HTTP {r.status_code}: {r.text[:500]}")
            return r
        raise KsefError(f"{method} {path}: przekroczono liczbę prób (429)")

    def _token_encryption_cert(self):
        certs = self._req("GET", "/security/public-key-certificates").json()
        now = dt.datetime.now(dt.timezone.utc)
        for c in certs:
            if "KsefTokenEncryption" not in c.get("usage", []):
                continue
            vf, vt = parse_iso(c["validFrom"]), parse_iso(c["validTo"])
            if vf <= now <= vt:
                return c
        raise KsefError("Brak ważnego certyfikatu MF do szyfrowania tokena (KsefTokenEncryption)")

    def login(self):
        """challenge -> ksef-token (RSA-OAEP SHA-256 z 'token|timestampMs') -> status -> redeem"""
        cert = self._token_encryption_cert()
        ch = self._req("POST", "/auth/challenge").json()

        pub = x509.load_der_x509_certificate(base64.b64decode(cert["certificate"])).public_key()
        plaintext = f"{self.token}|{ch['timestampMs']}".encode()
        encrypted = pub.encrypt(plaintext, padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(), label=None))

        body = {
            "challenge": ch["challenge"],
            "contextIdentifier": {"type": "Nip", "value": self.nip},
            "encryptedToken": base64.b64encode(encrypted).decode(),
            "publicKeyId": cert.get("publicKeyId"),
        }
        init = self._req("POST", "/auth/ksef-token", json=body).json()
        self._finish_auth(init)

    def login_cert(self, cert, key):
        """challenge -> AuthTokenRequest podpisany XAdES -> /auth/xades-signature -> status -> redeem"""
        from lxml import etree
        from cryptography.hazmat.primitives.asymmetric.ec import EllipticCurvePrivateKey
        from signxml.algorithms import DigestAlgorithm, SignatureConstructionMethod, SignatureMethod
        from signxml.xades.xades import XAdESSigner

        ch = self._req("POST", "/auth/challenge").json()
        NS = "http://ksef.mf.gov.pl/auth/token/2.0"
        root = etree.Element(f"{{{NS}}}AuthTokenRequest", nsmap={None: NS})
        etree.SubElement(root, f"{{{NS}}}Challenge").text = ch["challenge"]
        ctx = etree.SubElement(root, f"{{{NS}}}ContextIdentifier")
        etree.SubElement(ctx, f"{{{NS}}}Nip").text = self.nip
        etree.SubElement(root, f"{{{NS}}}SubjectIdentifierType").text = "certificateSubject"

        signer = XAdESSigner(
            method=SignatureConstructionMethod.enveloped,
            signature_algorithm=(SignatureMethod.ECDSA_SHA256 if isinstance(key, EllipticCurvePrivateKey)
                                 else SignatureMethod.RSA_SHA256),
            digest_algorithm=DigestAlgorithm.SHA256,
        )
        signed = etree.tostring(signer.sign(data=root, key=key, cert=[cert]),
                                xml_declaration=True, encoding="UTF-8")
        init = self._req("POST", "/auth/xades-signature", data=signed,
                         params={"verifyCertificateChain": "false"},
                         headers={"Content-Type": "application/xml"}).json()
        self._finish_auth(init)

    def _finish_auth(self, init):
        ref = init["referenceNumber"]
        auth_tok = init["authenticationToken"]["token"]

        deadline = time.time() + 120
        while True:
            st = self._req("GET", f"/auth/{ref}", bearer=auth_tok).json()["status"]
            if st["code"] == 200:
                break
            if st["code"] != 100:
                raise KsefError(f"Uwierzytelnienie nieudane: {st['code']} {st.get('description')} "
                                f"{st.get('details') or ''}")
            if time.time() > deadline:
                raise KsefError("Uwierzytelnienie: przekroczono czas oczekiwania")
            time.sleep(1)

        tokens = self._req("POST", "/auth/token/redeem", bearer=auth_tok).json()
        self.access_token = tokens["accessToken"]["token"]

    def query_metadata(self, subject_type, date_from, date_to, date_type="Issue"):
        """Zwraca listę metadanych faktur. Okres dzielony na miesiące (limit API)."""
        wyniki = {}
        for a, b in month_chunks(date_from, date_to):
            filtr = {
                "subjectType": subject_type,
                "dateRange": {
                    "dateType": date_type,
                    "from": warsaw_iso(a, dt.time(0, 0, 0)),
                    "to": warsaw_iso(b, dt.time(23, 59, 59)),
                },
            }
            offset, ostatnia_granica = 0, None
            while True:
                r = self._req("POST", "/invoices/query/metadata", bearer=self.access_token,
                              params={"pageOffset": offset, "pageSize": 250, "sortOrder": "Asc"},
                              json=filtr).json()
                for inv in r.get("invoices", []):
                    wyniki[inv["ksefNumber"]] = inv
                if not r.get("hasMore"):
                    break
                if r.get("isTruncated"):
                    # limit 10 000 wyników - zawężamy zakres od ostatniej daty
                    if not r.get("invoices"):
                        raise KsefError("KSeF zwrócił obciętą stronę bez faktur")
                    last = r["invoices"][-1]
                    granica = {"Issue": last["issueDate"],
                               "Invoicing": last["invoicingDate"],
                               "PermanentStorage": last["permanentStorageDate"]}[date_type]
                    if granica == ostatnia_granica:
                        raise KsefError("Za dużo faktur w jednym dniu - zawęź zakres")
                    ostatnia_granica = granica
                    if len(granica) == 10:
                        granica = warsaw_iso(dt.date.fromisoformat(granica), dt.time(0, 0, 0))
                    filtr["dateRange"]["from"] = granica
                    offset = 0
                else:
                    offset += 1
        return list(wyniki.values())

    def download_xml(self, ksef_number):
        return self._req("GET", f"/invoices/ksef/{ksef_number}", bearer=self.access_token).content


def parse_iso(s):
    """ISO 8601 -> datetime; działa też na Pythonie 3.10 ('Z', 1-9 cyfr ułamka sekund)."""
    s = s.strip().replace("Z", "+00:00")
    m = re.match(r"^(.*T\d{2}:\d{2}:\d{2})(?:\.(\d+))?(.*)$", s)
    if m:
        frac = (m.group(2) or "")[:6]
        s = m.group(1) + (("." + frac.ljust(6, "0")) if frac else "") + m.group(3)
    d = dt.datetime.fromisoformat(s)
    return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)


def warsaw_iso(d, t):
    """Data+czas w strefie Europe/Warsaw (uwzględnia czas letni/zimowy)."""
    return dt.datetime.combine(d, t, tzinfo=ZoneInfo("Europe/Warsaw")).isoformat()


def month_chunks(d_from, d_to):
    a = d_from
    while a <= d_to:
        nxt = (a.replace(day=1) + dt.timedelta(days=32)).replace(day=1)
        b = min(nxt - dt.timedelta(days=1), d_to)
        yield a, b
        a = b + dt.timedelta(days=1)


# --------------------------------------------------------------------------
# Parser XML FA(3) - odporny na namespace (dopasowanie po nazwie lokalnej)
# --------------------------------------------------------------------------
def _local(tag):
    return tag.rsplit("}", 1)[-1]


def child(el, *path):
    """Zwraca element po ścieżce nazw lokalnych (pierwszy pasujący)."""
    cur = el
    for name in path:
        if cur is None:
            return None
        cur = next((c for c in cur if _local(c.tag) == name), None)
    return cur


def children(el, name):
    return [c for c in el if _local(c.tag) == name] if el is not None else []


def text(el, *path):
    e = child(el, *path)
    return e.text.strip() if e is not None and e.text else ""


def dec(el, *path):
    t = text(el, *path)
    if not t:
        return None
    try:
        return Decimal(t)
    except InvalidOperation:
        return None


def to_date(s):
    try:
        return dt.date.fromisoformat(s[:10]) if s else None
    except ValueError:
        return None


def podmiot_ident(p):
    """NIP / VAT UE / inny identyfikator podmiotu."""
    di = child(p, "DaneIdentyfikacyjne")
    if di is None:
        return "", ""
    nazwa = text(di, "Nazwa")
    if text(di, "NIP"):
        return text(di, "NIP"), nazwa
    if text(di, "NrVatUE"):
        return text(di, "KodUE") + text(di, "NrVatUE"), nazwa
    if text(di, "NrID"):
        return (text(di, "KodKraju") + " " + text(di, "NrID")).strip(), nazwa
    return "brak ID", nazwa


def adres(p):
    a = child(p, "Adres")
    if a is None:
        return ""
    return ", ".join(x for x in (text(a, "AdresL1"), text(a, "AdresL2"), text(a, "KodKraju")) if x)


def parse_fa(xml_bytes):
    root = ET.fromstring(xml_bytes)
    if _local(root.tag) != "Faktura":
        raise ValueError(f"To nie jest faktura KSeF (root: {_local(root.tag)})")
    fa = child(root, "Fa")
    if fa is None:
        raise ValueError("Brak sekcji <Fa>")
    p1, p2 = child(root, "Podmiot1"), child(root, "Podmiot2")
    s_nip, s_nazwa = podmiot_ident(p1)
    n_nip, n_nazwa = podmiot_ident(p2)

    waluta = text(fa, "KodWaluty") or "PLN"
    netto, vat, vat_pln = {}, {}, {}
    for pn, pv, pw, etyk in STAWKI:
        netto[etyk] = dec(fa, pn)
        vat[etyk] = dec(fa, pv) if pv else None
        if pv:
            vat_pln[etyk] = dec(fa, pw) if (pw and waluta != "PLN") else vat[etyk]

    netto_razem = sum((v for v in netto.values() if v is not None), ZERO)
    vat_razem = sum((v for v in vat.values() if v is not None), ZERO)
    vat_razem_pln = sum((v for v in vat_pln.values() if v is not None), ZERO)
    brutto = dec(fa, "P_15")

    # data sprzedaży: P_6 albo okres P_6_Od - P_6_Do
    data_sprz = text(fa, "P_6")
    if not data_sprz and child(fa, "OkresFa") is not None:
        data_sprz = f"{text(fa, 'OkresFa', 'P_6_Od')} - {text(fa, 'OkresFa', 'P_6_Do')}"

    adn = child(fa, "Adnotacje")
    plat = child(fa, "Platnosc")
    terminy = [text(t, "Termin") for t in children(plat, "TerminPlatnosci") if text(t, "Termin")]
    rachunki = [text(r, "NrRB") for r in children(plat, "RachunekBankowy") if text(r, "NrRB")]
    korekty = children(fa, "DaneFaKorygowanej")

    rodzaj = text(fa, "RodzajFaktury")
    rec = {
        "nr_faktury": text(fa, "P_2"),
        "rodzaj": f"{rodzaj} - {RODZAJ_FAKTURY.get(rodzaj, '')}".strip(" -"),
        "data_wyst": to_date(text(fa, "P_1")),
        "data_sprz": data_sprz,
        "miejsce": text(fa, "P_1M"),
        "sprzedawca_nip": s_nip, "sprzedawca": s_nazwa, "sprzedawca_adres": adres(p1),
        "nabywca_nip": n_nip, "nabywca": n_nazwa, "nabywca_adres": adres(p2),
        "waluta": waluta,
        "kurs": dec(fa, "KursWalutyZ"),
        "netto": netto, "vat": vat,
        "netto_razem": netto_razem, "vat_razem": vat_razem, "vat_razem_pln": vat_razem_pln,
        "brutto": brutto,
        "kontrola": (netto_razem + vat_razem - brutto) if brutto is not None else None,
        "termin": ", ".join(terminy),
        "forma": FORMA_PLATNOSCI.get(text(plat, "FormaPlatnosci"), text(plat, "FormaPlatnosci")),
        "zaplacono": ("TAK " + text(plat, "DataZaplaty")).strip() if text(plat, "Zaplacono") == "1"
                     else ("częściowo" if text(plat, "ZnacznikZaplatyCzesciowej") == "1" else ""),
        "rachunek": ", ".join(rachunki),
        "mpp": "TAK" if text(adn, "P_18A") == "1" else "",
        "kasowa": "TAK" if text(adn, "P_16") == "1" else "",
        "odwrotne": "TAK" if text(adn, "P_18") == "1" else "",
        "kor_nr": ", ".join(text(k, "NrFaKorygowanej") for k in korekty),
        "kor_ksef": ", ".join(text(k, "NrKSeFFaKorygowanej") for k in korekty),
        "kor_przyczyna": text(fa, "PrzyczynaKorekty"),
        "pozycje": [],
    }
    for w in children(fa, "FaWiersz"):
        rec["pozycje"].append({
            "lp": text(w, "NrWierszaFa"), "nazwa": text(w, "P_7"), "jm": text(w, "P_8A"),
            "ilosc": dec(w, "P_8B"), "cena_netto": dec(w, "P_9A"), "cena_brutto": dec(w, "P_9B"),
            "wartosc_netto": dec(w, "P_11"), "wartosc_brutto": dec(w, "P_11A"),
            "stawka": text(w, "P_12"), "gtu": text(w, "GTU"),
        })
    return rec


# --------------------------------------------------------------------------
# PDF -> numer KSeF (tylko tekst z PDF, bez OCR)
# --------------------------------------------------------------------------
def ksef_numbers_from_pdf(path):
    from pypdf import PdfReader
    txt = "\n".join((p.extract_text() or "") for p in PdfReader(str(path)).pages)
    found = KSEF_NR_RE.findall(txt.upper())
    if not found:  # numer mógł zostać złamany w linii
        found = KSEF_NR_RE.findall(re.sub(r"\s+", "", txt.upper()))
    return list(dict.fromkeys(found))


# --------------------------------------------------------------------------
# Raport Excel
# --------------------------------------------------------------------------
HDR_FILL = PatternFill("solid", fgColor="D9E1F2")
WARN_FILL = PatternFill("solid", fgColor="F8CBAD")
MONEY = '#,##0.00;[Red]-#,##0.00'


def kierunek(rec, nip):
    if nip and rec["sprzedawca_nip"] == nip:
        return "Sprzedaż"
    if nip and rec["nabywca_nip"] == nip:
        return "Zakup"
    return "Inny"


def write_xlsx(records, errors, nip, out_path, okres_opis):
    wb = Workbook()
    ws = wb.active
    ws.title = "Zestawienie"

    stawki_cols = []
    for _, pv, _, etyk in STAWKI:
        stawki_cols.append((f"Netto {etyk}", "netto", etyk))
        if pv:
            stawki_cols.append((f"VAT {etyk}", "vat", etyk))

    cols = (["Lp", "Kierunek", "Nr KSeF", "Nr faktury", "Rodzaj", "Data wystawienia",
             "Data sprzedaży", "Data nadania nr KSeF", "NIP sprzedawcy", "Sprzedawca",
             "NIP nabywcy", "Nabywca", "Waluta", "Kurs"]
            + [c[0] for c in stawki_cols]
            + ["Netto razem", "VAT razem", "VAT razem PLN", "Brutto (P_15)",
               "Kontrola netto+VAT-brutto", "Termin płatności", "Forma płatności", "Zapłacono",
               "Rachunek", "MPP", "Metoda kasowa", "Odwrotne obciążenie",
               "Nr faktury korygowanej", "Nr KSeF fakt. korygowanej", "Przyczyna korekty",
               "Plik źródłowy"])
    ws.append([f"Zestawienie faktur KSeF - {okres_opis} - NIP {nip or '?'} - "
               f"wygenerowano {dt.datetime.now():%Y-%m-%d %H:%M}"])
    ws["A1"].font = Font(bold=True, size=12)
    ws.append(cols)
    HR = 2
    for c in ws[HR]:
        c.font = Font(bold=True)
        c.fill = HDR_FILL
        c.alignment = Alignment(wrap_text=True, vertical="center")

    records.sort(key=lambda r: (kierunek(r, nip), r["data_wyst"] or dt.date.min, r["nr_faktury"]))
    money_idx = {i + 1 for i, h in enumerate(cols)
                 if h.startswith(("Netto", "VAT", "Brutto", "Kontrola"))}
    date_idx = {i + 1 for i, h in enumerate(cols) if h in ("Data wystawienia", "Data nadania nr KSeF")}

    for i, r in enumerate(records, 1):
        row = [i, kierunek(r, nip), r.get("ksef_nr", ""), r["nr_faktury"], r["rodzaj"],
               r["data_wyst"], r["data_sprz"], r.get("data_ksef"), r["sprzedawca_nip"],
               r["sprzedawca"], r["nabywca_nip"], r["nabywca"], r["waluta"], r["kurs"]]
        row += [r[kind][etyk] for _, kind, etyk in stawki_cols]
        row += [r["netto_razem"], r["vat_razem"], r["vat_razem_pln"], r["brutto"], r["kontrola"],
                r["termin"], r["forma"], r["zaplacono"], r["rachunek"], r["mpp"], r["kasowa"],
                r["odwrotne"], r["kor_nr"], r["kor_ksef"], r["kor_przyczyna"], r.get("plik", "")]
        ws.append([float(v) if isinstance(v, Decimal) else v for v in row])
        rr = ws.max_row
        for ci in money_idx:
            ws.cell(rr, ci).number_format = MONEY
        for ci in date_idx:
            ws.cell(rr, ci).number_format = "yyyy-mm-dd"
        k = r["kontrola"]
        if k is not None and abs(k) > Decimal("0.01"):
            ws.cell(rr, cols.index("Kontrola netto+VAT-brutto") + 1).fill = WARN_FILL

    last = ws.max_row
    # wiersz sum - SUBTOTAL, żeby liczył tylko widoczne po filtrowaniu
    ws.append([])
    ws.append(["SUMA (widoczne)"])
    sr = ws.max_row
    ws.cell(sr, 1).font = Font(bold=True)
    for ci in money_idx:
        if cols[ci - 1].startswith("Kontrola"):
            continue
        L = get_column_letter(ci)
        c = ws.cell(sr, ci, f"=SUBTOTAL(9,{L}{HR + 1}:{L}{max(last, HR + 1)})")
        c.number_format = MONEY
        c.font = Font(bold=True)

    ws.auto_filter.ref = f"A{HR}:{get_column_letter(len(cols))}{max(last, HR + 1)}"
    ws.freeze_panes = ws.cell(HR + 1, 5)
    widths = {"Sprzedawca": 35, "Nabywca": 35, "Nr KSeF": 38, "Nr faktury": 20, "Rodzaj": 18,
              "Rachunek": 30, "Przyczyna korekty": 30, "Plik źródłowy": 30}
    for i, h in enumerate(cols, 1):
        ws.column_dimensions[get_column_letter(i)].width = widths.get(h, 14)
    ws.row_dimensions[HR].height = 45

    # --- Podsumowanie wg kierunku i stawek (formuły SUMIFS do arkusza Zestawienie) ---
    ps = wb.create_sheet("Podsumowanie")
    ps.append(["Podsumowanie wg kierunku (kwoty w walucie faktury; VAT razem PLN - w PLN)"])
    ps["A1"].font = Font(bold=True, size=12)
    ps.append(["Pozycja", "Zakup", "Sprzedaż", "Inny"])
    for c in ps[2]:
        c.font = Font(bold=True)
        c.fill = HDR_FILL
    kcol = get_column_letter(cols.index("Kierunek") + 1)
    rng = lambda L: f"Zestawienie!${L}${HR + 1}:${L}${max(last, HR + 1)}"
    sum_headers = [h for h in cols if h.startswith(("Netto", "VAT", "Brutto"))]
    for h in sum_headers:
        L = get_column_letter(cols.index(h) + 1)
        row = [h] + [f'=SUMIFS({rng(L)},{rng(kcol)},"{k}")' for k in ("Zakup", "Sprzedaż", "Inny")]
        ps.append(row)
        for c in ps[ps.max_row][1:]:
            c.number_format = MONEY
    ps.append(["Liczba faktur"] + [f'=COUNTIF({rng(kcol)},"{k}")' for k in ("Zakup", "Sprzedaż", "Inny")])
    walutowe = sum(1 for r in records if r["waluta"] != "PLN")
    if walutowe:
        ps.append([])
        ps.append([f"Uwaga: {walutowe} faktur w walucie obcej - sumy netto/brutto mieszają waluty, "
                   f"filtruj po kolumnie Waluta."])
    ps.column_dimensions["A"].width = 34
    for L in "BCD":
        ps.column_dimensions[L].width = 18

    # --- Pozycje faktur ---
    pz = wb.create_sheet("Pozycje")
    pcols = ["Nr KSeF", "Nr faktury", "Sprzedawca", "Lp", "Nazwa towaru/usługi", "J.m.", "Ilość",
             "Cena netto", "Cena brutto", "Wartość netto", "Wartość brutto", "Stawka VAT", "GTU"]
    pz.append(pcols)
    for c in pz[1]:
        c.font = Font(bold=True)
        c.fill = HDR_FILL
    for r in records:
        for p in r["pozycje"]:
            row = [r.get("ksef_nr", ""), r["nr_faktury"], r["sprzedawca"], p["lp"], p["nazwa"], p["jm"],
                   p["ilosc"], p["cena_netto"], p["cena_brutto"], p["wartosc_netto"],
                   p["wartosc_brutto"], p["stawka"], p["gtu"]]
            pz.append([float(v) if isinstance(v, Decimal) else v for v in row])
            for ci in (8, 9, 10, 11):
                pz.cell(pz.max_row, ci).number_format = MONEY
    pz.auto_filter.ref = f"A1:M{max(pz.max_row, 2)}"
    pz.freeze_panes = "A2"
    for i, w in enumerate([38, 20, 30, 5, 50, 8, 10, 12, 12, 14, 14, 10, 8], 1):
        pz.column_dimensions[get_column_letter(i)].width = w

    # --- Błędy ---
    if errors:
        er = wb.create_sheet("Błędy")
        er.append(["Plik / numer KSeF", "Problem"])
        for c in er[1]:
            c.font = Font(bold=True)
        for e in errors:
            er.append(list(e))
        er.column_dimensions["A"].width = 45
        er.column_dimensions["B"].width = 90

    out_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out_path)
    return out_path


# --------------------------------------------------------------------------
# Tryby
# --------------------------------------------------------------------------
def load_cert_and_key(cfg):
    """Wczytuje certyfikat KSeF i klucz prywatny (PEM/DER + PEM albo PKCS#12)."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.serialization import pkcs12

    pwd = cfg.get("KSEF_KEY_PASSWORD")
    pwd = pwd.encode() if pwd else None
    try:
        if cfg.get("KSEF_P12"):
            key, cert, _ = pkcs12.load_key_and_certificates(Path(cfg["KSEF_P12"]).read_bytes(), pwd)
            if cert is None or key is None:
                sys.exit("Plik .p12 nie zawiera certyfikatu lub klucza prywatnego")
        else:
            if not cfg.get("KSEF_CERT") or not cfg.get("KSEF_KEY"):
                sys.exit("Dla KSEF_AUTH=cert podaj KSEF_CERT i KSEF_KEY (albo KSEF_P12).")
            raw = Path(cfg["KSEF_CERT"]).read_bytes()
            cert = (x509.load_pem_x509_certificate(raw) if b"-----BEGIN" in raw
                    else x509.load_der_x509_certificate(raw))
            key = serialization.load_pem_private_key(Path(cfg["KSEF_KEY"]).read_bytes(), password=pwd)
    except FileNotFoundError as e:
        sys.exit(f"Nie znaleziono pliku: {e.filename}")
    except (ValueError, TypeError) as e:
        sys.exit(f"Nie można wczytać certyfikatu/klucza (złe hasło lub format?): {e}")

    # zgodność klucza z certyfikatem
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    if (cert.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
            != key.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)):
        sys.exit("Klucz prywatny nie pasuje do certyfikatu.")

    # ważność certyfikatu
    now = dt.datetime.now(dt.timezone.utc)
    nva = getattr(cert, "not_valid_after_utc", None) or cert.not_valid_after.replace(tzinfo=dt.timezone.utc)
    nvb = getattr(cert, "not_valid_before_utc", None) or cert.not_valid_before.replace(tzinfo=dt.timezone.utc)
    if now > nva:
        sys.exit(f"Certyfikat KSeF wygasł {nva:%Y-%m-%d}.")
    if now < nvb:
        sys.exit(f"Certyfikat KSeF ważny dopiero od {nvb:%Y-%m-%d}.")
    if (nva - now).days < 30:
        print(f"UWAGA: certyfikat KSeF wygasa {nva:%Y-%m-%d} ({(nva - now).days} dni).", file=sys.stderr)
    return cert, key


def need_api(cfg):
    if not cfg.get("KSEF_NIP"):
        sys.exit("Brak KSEF_NIP (ksef.conf albo zmienne środowiskowe).")
    c = KsefClient(cfg["KSEF_NIP"], cfg.get("KSEF_TOKEN"), cfg["KSEF_ENV"])
    if cfg.get("KSEF_AUTH", "token") == "cert":
        cert, key = load_cert_and_key(cfg)
        print(f"Logowanie do KSeF ({cfg['KSEF_ENV']}) certyfikatem jako NIP {cfg['KSEF_NIP']}...")
        c.login_cert(cert, key)
    else:
        if not cfg.get("KSEF_TOKEN"):
            sys.exit("Brak KSEF_TOKEN (albo ustaw KSEF_AUTH=cert i podaj certyfikat).")
        if dt.date.today() >= dt.date(2027, 1, 1):
            print("UWAGA: od 2027 r. tokeny KSeF nie są obsługiwane - użyj certyfikatu.", file=sys.stderr)
        print(f"Logowanie do KSeF ({cfg['KSEF_ENV']}) tokenem jako NIP {cfg['KSEF_NIP']}...")
        c.login()
    return c


def cmd_pobierz(args, cfg):
    d_from, d_to = dt.date.fromisoformat(args.od), dt.date.fromisoformat(args.do)
    if d_to < d_from:
        sys.exit("Data 'do' wcześniejsza niż 'od'")
    client = need_api(cfg)
    date_type = {"wystawienia": "Issue", "ksef": "PermanentStorage"}[args.data]
    typy = {"zakup": ["Subject2"], "sprzedaz": ["Subject1"], "oba": ["Subject2", "Subject1"]}[args.typ]

    xml_dir = Path(args.out) / "xml"
    xml_dir.mkdir(parents=True, exist_ok=True)
    records, errors = [], []
    for st in typy:
        meta = client.query_metadata(st, d_from, d_to, date_type)
        print(f"{'Zakup' if st == 'Subject2' else 'Sprzedaż'}: {len(meta)} faktur")
        for m in meta:
            nr = m["ksefNumber"]
            f = xml_dir / f"{nr}.xml"
            try:
                if not f.exists():
                    f.write_bytes(client.download_xml(nr))
                rec = parse_fa(f.read_bytes())
                rec.update(ksef_nr=nr, plik=f.name, data_ksef=to_date(m.get("acquisitionDate", "")))
                records.append(rec)
            except Exception as e:
                errors.append((nr, str(e)))
    out = Path(args.out) / f"zestawienie_KSeF_{args.typ}_{d_from}_{d_to}.xlsx"
    write_xlsx(records, errors, cfg.get("KSEF_NIP"), out, f"{d_from} - {d_to}")
    print(f"Raport: {out}  (faktur: {len(records)}, błędów: {len(errors)})")


def cmd_pliki(args, cfg):
    src = Path(args.folder)
    files = sorted(p for p in src.rglob("*") if p.suffix.lower() in (".xml", ".pdf"))
    if not files:
        sys.exit(f"Brak plików .xml/.pdf w {src}")
    records, errors, client = [], [], None
    xml_dir = Path(args.out) / "xml"
    seen = set()

    def add(xml_bytes, nr, plik):
        rec = parse_fa(xml_bytes)
        rec.update(ksef_nr=nr, plik=plik)
        key = nr or (rec["sprzedawca_nip"], rec["nr_faktury"])
        if key in seen:
            return
        seen.add(key)
        records.append(rec)

    for p in files:
        try:
            if p.suffix.lower() == ".xml":
                m = KSEF_NR_RE.search(p.name.upper())
                add(p.read_bytes(), m.group(0) if m else "", p.name)
            else:
                nums = ksef_numbers_from_pdf(p)
                if not nums:
                    errors.append((p.name, "Nie znaleziono numeru KSeF w tekście PDF (skan? brak warstwy tekstowej)"))
                    continue
                if client is None:
                    client = need_api(cfg)
                    xml_dir.mkdir(parents=True, exist_ok=True)
                for nr in nums:
                    f = xml_dir / f"{nr}.xml"
                    if not f.exists():
                        f.write_bytes(client.download_xml(nr))
                    add(f.read_bytes(), nr, p.name)
        except Exception as e:
            errors.append((p.name, str(e)))
    out = Path(args.out) / f"zestawienie_KSeF_pliki_{dt.date.today()}.xlsx"
    write_xlsx(records, errors, cfg.get("KSEF_NIP"), out, f"pliki z {src.name}")
    print(f"Raport: {out}  (faktur: {len(records)}, błędów: {len(errors)})")


def cmd_serwuj(args, cfg):
    d = Path(args.out).resolve()
    d.mkdir(parents=True, exist_ok=True)
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(d))
    with socketserver.TCPServer((args.host, args.port), handler) as srv:
        print(f"Raporty do pobrania: http://{args.host}:{args.port}/  (folder {d}), Ctrl+C = stop")
        srv.serve_forever()


def main():
    cfg = load_config()
    ap = argparse.ArgumentParser(description="Zestawienie faktur z KSeF dla księgowej (.xlsx)")
    ap.add_argument("--out", default="raporty", help="folder wyjściowy (domyślnie ./raporty)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("pobierz", help="pobierz faktury z API KSeF za okres")
    p.add_argument("--od", required=True, help="RRRR-MM-DD")
    p.add_argument("--do", required=True, help="RRRR-MM-DD")
    p.add_argument("--typ", choices=["zakup", "sprzedaz", "oba"], default="zakup")
    p.add_argument("--data", choices=["wystawienia", "ksef"], default="wystawienia",
                   help="filtr po dacie wystawienia albo dacie zapisu w KSeF")
    p.set_defaults(fn=cmd_pobierz)

    p = sub.add_parser("pliki", help="zestawienie z folderu z plikami .xml/.pdf z KSeF")
    p.add_argument("folder")
    p.set_defaults(fn=cmd_pliki)

    p = sub.add_parser("serwuj", help="udostępnij raporty przez HTTP")
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=8080)
    p.set_defaults(fn=cmd_serwuj)

    args = ap.parse_args()
    args.fn(args, cfg)


if __name__ == "__main__":
    main()
