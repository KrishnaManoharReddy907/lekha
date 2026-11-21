"""Identity, tenancy and platform tables."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import BigInteger, Boolean, Date, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from lekha.models.base import Base, IdMixin, TenantMixin

ROLES = ("owner", "partner", "staff", "client", "viewer")


class Firm(IdMixin, Base):
    __tablename__ = "firms"
    name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(20), default="ca_firm")  # ca_firm | business
    settings: Mapped[dict] = mapped_column(default=dict)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)


class User(IdMixin, Base):
    __tablename__ = "users"
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    password_hash: Mapped[str] = mapped_column(String(300))
    language: Mapped[str] = mapped_column(String(5), default="en")
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Membership(IdMixin, Base):
    __tablename__ = "memberships"
    __table_args__ = (UniqueConstraint("firm_id", "user_id"),)
    firm_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("firms.id", ondelete="CASCADE"))
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    role: Mapped[str] = mapped_column(String(20))
    # For role=client: the clients this user may see.
    client_ids: Mapped[list] = mapped_column(default=list)


class Session(IdMixin, Base):
    __tablename__ = "sessions"
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    firm_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("firms.id", ondelete="CASCADE"))
    csrf_token: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    user_agent: Mapped[str | None] = mapped_column(String(400))


class Invite(IdMixin, Base):
    __tablename__ = "invites"
    firm_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("firms.id", ondelete="CASCADE"))
    email: Mapped[str] = mapped_column(String(320))
    role: Mapped[str] = mapped_column(String(20))
    client_ids: Mapped[list] = mapped_column(default=list)
    token_hash: Mapped[str] = mapped_column(String(128), unique=True)
    invited_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ApiKey(IdMixin, Base):
    __tablename__ = "api_keys"
    firm_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("firms.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(100))
    prefix: Mapped[str] = mapped_column(String(16), index=True)
    key_hash: Mapped[str] = mapped_column(String(128), unique=True)
    role: Mapped[str] = mapped_column(String(20), default="staff")
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PublicLink(IdMixin, Base):
    """Login-free links: client upload links and supplier self-service links."""

    __tablename__ = "public_links"
    firm_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("firms.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(20))  # upload | supplier
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    client_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    supplier_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    label: Mapped[str | None] = mapped_column(String(200))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    use_count: Mapped[int] = mapped_column(Integer, default=0)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))


class Job(IdMixin, Base):
    __tablename__ = "jobs"
    __table_args__ = (Index("ix_jobs_pick", "status", "run_after"),)
    firm_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("firms.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(60))
    payload: Mapped[dict] = mapped_column(default=dict)
    status: Mapped[str] = mapped_column(String(20), default="queued")  # queued|running|done|failed|dead
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=4)
    run_after: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    result: Mapped[dict | None] = mapped_column()
    dedupe_key: Mapped[str | None] = mapped_column(String(200), index=True)


class RuleVersionRow(IdMixin, Base):
    """Persisted rule versions. firm_id NULL = platform rule; drafts may be firm-scoped."""

    __tablename__ = "rule_versions"
    __table_args__ = (UniqueConstraint("code", "version"),)
    firm_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("firms.id", ondelete="CASCADE"))
    code: Mapped[str] = mapped_column(String(60), index=True)
    version: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(300), default="")
    source: Mapped[str] = mapped_column(String(300), default="")
    effective_from: Mapped[date] = mapped_column(Date)
    effective_to: Mapped[date | None] = mapped_column(Date)
    params: Mapped[dict] = mapped_column(default=dict)
    status: Mapped[str] = mapped_column(String(20), default="published")
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuditLog(TenantMixin, Base):
    __tablename__ = "audit_log"
    actor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    actor_kind: Mapped[str] = mapped_column(String(20), default="user")  # user|system|api|public
    action: Mapped[str] = mapped_column(String(80))
    entity: Mapped[str] = mapped_column(String(60))
    entity_id: Mapped[str | None] = mapped_column(String(64), index=True)
    before: Mapped[dict | None] = mapped_column()
    after: Mapped[dict | None] = mapped_column()
    meta: Mapped[dict] = mapped_column(default=dict)


class Notification(TenantMixin, Base):
    __tablename__ = "notifications"
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    client_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    kind: Mapped[str] = mapped_column(String(40))
    title: Mapped[str] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text, default="")
    link: Mapped[str | None] = mapped_column(String(300))
    severity: Mapped[str] = mapped_column(String(10), default="info")
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Client(TenantMixin, Base):
    """A business whose books the firm manages (or the firm itself for kind=business)."""

    __tablename__ = "clients"
    name: Mapped[str] = mapped_column(String(200))
    trade_name: Mapped[str | None] = mapped_column(String(200))
    contact_name: Mapped[str | None] = mapped_column(String(200))
    contact_email: Mapped[str | None] = mapped_column(String(320))
    contact_phone: Mapped[str | None] = mapped_column(String(30))
    language: Mapped[str] = mapped_column(String(5), default="en")
    filing_frequency: Mapped[str] = mapped_column(String(10), default="monthly")  # monthly|quarterly
    settings: Mapped[dict] = mapped_column(default=dict)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)


class ClientGstin(TenantMixin, Base):
    __tablename__ = "client_gstins"
    __table_args__ = (UniqueConstraint("firm_id", "gstin"),)
    client_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"),
                                                 index=True)
    gstin: Mapped[str] = mapped_column(String(15))
    state_code: Mapped[str] = mapped_column(String(2))
    legal_name: Mapped[str | None] = mapped_column(String(200))
    is_primary: Mapped[bool] = mapped_column(Boolean, default=True)
    aato: Mapped[int | None] = mapped_column(BigInteger)  # aggregate annual turnover, rupees (optional)
