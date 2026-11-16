"""Wątek w tle przetwarzający raporty po kolei (wersja testowa: 1 proces backendu).

Pobieranie może trwać długo (limity API KSeF), dlatego raport działa w tle,
a frontend odpytuje jego status.
"""
import datetime as dt
import logging
import threading

from sqlalchemy import select, update

from . import config
from .db import SessionLocal
from .ksef_service import CertError, run_report
from .models import Report

log = logging.getLogger("worker")
_event = threading.Event()
_started = False


def wake():
    _event.set()


def _set(rid, **kw):
    with SessionLocal() as db:
        db.execute(update(Report).where(Report.id == rid).values(**kw))
        db.commit()


def _process(rid):
    with SessionLocal() as db:
        r = db.get(Report, rid)
        company = r.user.company
        user_id, args = r.user_id, (r.date_from, r.date_to, r.kind, r.date_type)
        db.expunge(company)

    _set(rid, status="running", progress="Start")
    base = config.DATA_DIR / "users" / str(user_id)
    out = base / "reports" / f"zestawienie_KSeF_{args[2]}_{args[0]}_{args[1]}_{rid}.xlsx"
    try:
        n, e = run_report(company, *args, xml_dir=base / "xml" / company.nip, out_path=out,
                          progress=lambda t: _set(rid, progress=t[:500]))
        _set(rid, status="done", progress="Gotowe", invoices=n, errors=e, file_path=str(out),
             finished_at=dt.datetime.now(dt.timezone.utc))
    except CertError as ex:
        _set(rid, status="error", progress=str(ex)[:500], finished_at=dt.datetime.now(dt.timezone.utc))
    except Exception as ex:
        log.exception("Raport %s nieudany", rid)
        _set(rid, status="error", progress=f"Błąd (KSeF {company.ksef_env}): {ex}"[:500],
             finished_at=dt.datetime.now(dt.timezone.utc))


def _loop():
    while True:
        _event.wait(timeout=10)
        _event.clear()
        while True:
            with SessionLocal() as db:
                rid = db.scalar(select(Report.id).where(Report.status == "queued").order_by(Report.id))
            if rid is None:
                break
            _process(rid)


def start():
    global _started
    if _started:
        return
    _started = True
    # raporty przerwane restartem wracają do kolejki
    with SessionLocal() as db:
        db.execute(update(Report).where(Report.status == "running")
                   .values(status="queued", progress="Wznowiono po restarcie"))
        db.commit()
    threading.Thread(target=_loop, daemon=True, name="report-worker").start()
    wake()
