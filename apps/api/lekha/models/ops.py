"""Follow-ups, graph signals, notices, close runs, Table 4 snapshots, copilot log, rule impact."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from lekha.models.base import Base, Money, TenantMixin


class FollowUp(TenantMixin, Base):
    __tablename__ = "followups"
    supplier_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("suppliers.id", ondelete="CASCADE"),
                                                   index=True)
    client_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"))
    channel: Mapped[str] = mapped_column(String(20), default="email")  # email | whatsapp | phone | portal
    language: Mapped[str] = mapped_column(String(5), default="en")
    subject: Mapped[str] = mapped_column(String(300), default="")
    body: Mapped[str] = mapped_column(Text, default="")
    items: Mapped[list] = mapped_column(default=list)  # [{invoice_id|record_id, issue, amount}]
    amount: Mapped[Decimal] = mapped_column(Money, default=0)
    status: Mapped[str] = mapped_column(String(20), default="draft")  # draft|sent|responded|resolved|escalated
    escalation_level: Mapped[int] = mapped_column(Integer, default=0)
    link_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_action_on: Mapped[date | None] = mapped_column(Date)
    response: Mapped[dict | None] = mapped_column()
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))


class SupplierResponse(TenantMixin, Base):
    """What a supplier said/uploaded through their self-service link."""

    __tablename__ = "supplier_responses"
    supplier_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("suppliers.id", ondelete="CASCADE"))
    followup_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    item_ref: Mapped[str | None] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(30))  # will_file | filed | amended | disputed | uploaded
    message: Mapped[str] = mapped_column(Text, default="")
    document_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    expected_by: Mapped[date | None] = mapped_column(Date)


class GraphSignal(TenantMixin, Base):
    __tablename__ = "graph_signals"
    kind: Mapped[str] = mapped_column(String(30), index=True)  # cycle | shell | template_reuse | cross_client
    score: Mapped[float] = mapped_column(Float)
    title: Mapped[str] = mapped_column(String(300))
    detail: Mapped[str] = mapped_column(Text, default="")
    members: Mapped[list] = mapped_column(default=list)  # GSTINs
    evidence: Mapped[dict] = mapped_column(default=dict)
    affected_clients: Mapped[list] = mapped_column(default=list)
    amount: Mapped[Decimal] = mapped_column(Money, default=0)
    status: Mapped[str] = mapped_column(String(20), default="open")  # open | dismissed | confirmed
    fingerprint: Mapped[str] = mapped_column(String(200), index=True)


class Notice(TenantMixin, Base):
    __tablename__ = "notices"
    client_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"))
    client_gstin: Mapped[str] = mapped_column(String(15))
    form: Mapped[str] = mapped_column(String(20))  # DRC-01C | ASMT-10 | DRC-01B | OTHER
    reference: Mapped[str | None] = mapped_column(String(100))
    period_from: Mapped[str] = mapped_column(String(6))
    period_to: Mapped[str] = mapped_column(String(6))
    amount: Mapped[Decimal] = mapped_column(Money, default=0)
    received_on: Mapped[date | None] = mapped_column(Date)
    reply_due: Mapped[date | None] = mapped_column(Date)
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="open")
    document_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))


class NoticePack(TenantMixin, Base):
    __tablename__ = "notice_packs"
    notice_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("notices.id", ondelete="CASCADE"),
                                                 index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    analysis: Mapped[dict] = mapped_column(default=dict)
    reply_text: Mapped[str] = mapped_column(Text, default="")
    evidence: Mapped[list] = mapped_column(default=list)
    zip_key: Mapped[str | None] = mapped_column(String(400))
    pdf_key: Mapped[str | None] = mapped_column(String(400))
    status: Mapped[str] = mapped_column(String(20), default="draft")


class CloseRun(TenantMixin, Base):
    __tablename__ = "close_runs"
    client_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"),
                                                 index=True)
    client_gstin: Mapped[str] = mapped_column(String(15))
    period: Mapped[str] = mapped_column(String(6))
    status: Mapped[str] = mapped_column(String(20), default="running")  # running|awaiting_approval|approved|failed
    trigger: Mapped[str] = mapped_column(String(30), default="manual")
    summary: Mapped[dict] = mapped_column(default=dict)
    approved_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CloseStep(TenantMixin, Base):
    __tablename__ = "close_steps"
    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("close_runs.id", ondelete="CASCADE"),
                                              index=True)
    position: Mapped[int] = mapped_column(Integer)
    code: Mapped[str] = mapped_column(String(40))
    title: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20), default="pending")
    # pending | running | done | needs_approval | approved | skipped | failed
    output: Mapped[dict] = mapped_column(default=dict)
    approval_required: Mapped[bool] = mapped_column(default=False)
    approved_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)


class Table4Snapshot(TenantMixin, Base):
    __tablename__ = "table4_snapshots"
    client_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"))
    client_gstin: Mapped[str] = mapped_column(String(15))
    period: Mapped[str] = mapped_column(String(6), index=True)
    cells: Mapped[dict] = mapped_column(default=dict)
    sources: Mapped[dict] = mapped_column(default=dict)
    status: Mapped[str] = mapped_column(String(20), default="draft")  # draft | final
    finalised_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))


class CopilotMessage(TenantMixin, Base):
    __tablename__ = "copilot_messages"
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    client_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    conversation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    role: Mapped[str] = mapped_column(String(10))
    content: Mapped[str] = mapped_column(Text)
    tool_calls: Mapped[list] = mapped_column(default=list)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)


class RuleImpactReport(TenantMixin, Base):
    __tablename__ = "rule_impact_reports"
    draft_ids: Mapped[list] = mapped_column(default=list)
    summary: Mapped[dict] = mapped_column(default=dict)
    changes: Mapped[list] = mapped_column(default=list)
    status: Mapped[str] = mapped_column(String(20), default="running")
