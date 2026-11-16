import datetime as dt

from sqlalchemy import Date, DateTime, ForeignKey, Integer, LargeBinary, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def now():
    return dt.datetime.now(dt.timezone.utc)


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)  # None = tylko Google
    google_sub: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=now)

    company: Mapped["Company | None"] = relationship(back_populates="user", uselist=False,
                                                      cascade="all, delete-orphan")
    reports: Mapped[list["Report"]] = relationship(back_populates="user", cascade="all, delete-orphan")


class Company(Base):
    """Jeden NIP na konto. Certyfikat, klucz i hasło - zaszyfrowane (Fernet)."""
    __tablename__ = "companies"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True)
    nip: Mapped[str] = mapped_column(String(10))
    name: Mapped[str] = mapped_column(String(255), default="")
    ksef_env: Mapped[str] = mapped_column(String(10), default="prod")
    auth_method: Mapped[str] = mapped_column(String(10), default="cert")  # cert | token
    token_enc: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)

    cert_enc: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    key_enc: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    key_password_enc: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    cert_subject: Mapped[str] = mapped_column(String(500), default="")
    cert_valid_to: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)

    user: Mapped[User] = relationship(back_populates="company")


class Report(Base):
    __tablename__ = "reports"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    date_from: Mapped[dt.date] = mapped_column(Date)
    date_to: Mapped[dt.date] = mapped_column(Date)
    kind: Mapped[str] = mapped_column(String(10))        # zakup | sprzedaz | oba
    date_type: Mapped[str] = mapped_column(String(15))   # wystawienia | ksef
    status: Mapped[str] = mapped_column(String(15), default="queued")  # queued|running|done|error
    progress: Mapped[str] = mapped_column(Text, default="")
    invoices: Mapped[int] = mapped_column(Integer, default=0)
    errors: Mapped[int] = mapped_column(Integer, default=0)
    file_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=now)
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped[User] = relationship(back_populates="reports")
