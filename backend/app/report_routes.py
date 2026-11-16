"""Zlecanie, lista i pobieranie raportów."""
import datetime as dt
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import get_db
from .ksef_service import has_credentials
from .models import Report, User
from .security import current_user
from .worker import wake

router = APIRouter(prefix="/api/reports", tags=["reports"])

MAX_DAYS = 366


class ReportIn(BaseModel):
    date_from: dt.date
    date_to: dt.date
    kind: str = "oba"          # zakup | sprzedaz | oba
    date_type: str = "wystawienia"  # wystawienia | ksef


def report_out(r: Report):
    return {"id": r.id, "date_from": r.date_from.isoformat(), "date_to": r.date_to.isoformat(),
            "kind": r.kind, "date_type": r.date_type, "status": r.status, "progress": r.progress,
            "invoices": r.invoices, "errors": r.errors,
            "created_at": r.created_at.isoformat(),
            "finished_at": r.finished_at.isoformat() if r.finished_at else None,
            "downloadable": r.status == "done" and bool(r.file_path)}


@router.get("")
def list_reports(u: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(Report).where(Report.user_id == u.id).order_by(Report.id.desc()).limit(50))
    return [report_out(r) for r in rows]


@router.post("")
def create_report(data: ReportIn, u: User = Depends(current_user), db: Session = Depends(get_db)):
    c = u.company
    if not c or not has_credentials(c):
        raise HTTPException(400, "Uzupełnij NIP oraz certyfikat lub token KSeF w ustawieniach firmy")
    if data.kind not in ("zakup", "sprzedaz", "oba") or data.date_type not in ("wystawienia", "ksef"):
        raise HTTPException(400, "Nieprawidłowy rodzaj raportu")
    if data.date_to < data.date_from:
        raise HTTPException(400, "Data 'do' jest wcześniejsza niż 'od'")
    if data.date_to > dt.date.today():
        raise HTTPException(400, "Data 'do' nie może być w przyszłości")
    if (data.date_to - data.date_from).days > MAX_DAYS:
        raise HTTPException(400, f"Maksymalny zakres to {MAX_DAYS} dni")
    active = db.scalar(select(Report).where(Report.user_id == u.id,
                                            Report.status.in_(("queued", "running"))))
    if active:
        raise HTTPException(409, "Poprzedni raport jest jeszcze w trakcie - poczekaj na jego zakończenie")
    r = Report(user_id=u.id, date_from=data.date_from, date_to=data.date_to,
               kind=data.kind, date_type=data.date_type, progress="W kolejce")
    db.add(r)
    db.commit()
    wake()
    return report_out(r)


@router.get("/{rid}/download")
def download(rid: int, u: User = Depends(current_user), db: Session = Depends(get_db)):
    r = db.get(Report, rid)
    if not r or r.user_id != u.id:
        raise HTTPException(404, "Nie znaleziono raportu")
    if r.status != "done" or not r.file_path or not Path(r.file_path).exists():
        raise HTTPException(404, "Plik raportu jest niedostępny")
    return FileResponse(r.file_path, filename=Path(r.file_path).name,
                        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@router.delete("/{rid}")
def delete_report(rid: int, u: User = Depends(current_user), db: Session = Depends(get_db)):
    r = db.get(Report, rid)
    if not r or r.user_id != u.id:
        raise HTTPException(404, "Nie znaleziono raportu")
    if r.status == "running":
        raise HTTPException(409, "Nie można usunąć raportu w trakcie generowania")
    if r.file_path:
        Path(r.file_path).unlink(missing_ok=True)
    db.delete(r)
    db.commit()
    return {"ok": True}
