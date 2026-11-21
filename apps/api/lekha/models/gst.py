"""GST domain tables: suppliers, documents, invoices, GSTR-2B, matching, IMS, risk, payments."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from lekha.models.base import Base, Money, TenantMixin


class Supplier(TenantMixin, Base):
    """Firm-wide supplier master, shared by all of the firm's clients (enables cross-client intelligence)."""

    __tablename__ = "suppliers"
    __table_args__ = (UniqueConstraint("firm_id", "gstin"),)
    gstin: Mapped[str] = mapped_column(String(15))
    legal_name: Mapped[str | None] = mapped_column(String(300))
    trade_name: Mapped[str | None] = mapped_column(String(300))
    state_code: Mapped[str | None] = mapped_column(String(2))
    email: Mapped[str | None] = mapped_column(String(320))
    phone: Mapped[str | None] = mapped_column(String(30))
    language: Mapped[str] = mapped_column(String(5), default="en")
    msme_category: Mapped[str | None] = mapped_column(String(10))  # micro | small | medium
    udyam_number: Mapped[str | None] = mapped_column(String(30))
    has_payment_agreement: Mapped[bool] = mapped_column(Boolean, default=True)
    first_seen: Mapped[date | None] = mapped_column(Date)
    score: Mapped[float | None] = mapped_column(Float)
    score_detail: Mapped[dict] = mapped_column(default=dict)
    notes: Mapped[str | None] = mapped_column(Text)


class Document(TenantMixin, Base):
    __tablename__ = "documents"
    client_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"),
                                                 index=True)
    storage_key: Mapped[str] = mapped_column(String(400))
    filename: Mapped[str] = mapped_column(String(300))
    mime: Mapped[str] = mapped_column(String(100))
    size: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    phash: Mapped[str | None] = mapped_column(String(32), index=True)
    layout_hash: Mapped[str | None] = mapped_column(String(32), index=True)
    pages: Mapped[int] = mapped_column(Integer, default=1)
    source: Mapped[str] = mapped_column(String(20), default="upload")  # upload | camera | link | api | demo
    status: Mapped[str] = mapped_column(String(20), default="queued")  # queued|processing|extracted|failed|duplicate
    error: Mapped[str | None] = mapped_column(Text)
    duplicate_of: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    ocr: Mapped[dict | None] = mapped_column()  # {pages:[{w,h,tokens:[{id,text,box:[x0,y0,x1,y1],conf}]}]}
    page_images: Mapped[list] = mapped_column(default=list)  # storage keys of rendered page PNGs
    qr: Mapped[dict | None] = mapped_column()  # decoded e-invoice QR payload


class Extraction(TenantMixin, Base):
    __tablename__ = "extractions"
    document_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"),
                                                   index=True)
    engine: Mapped[str] = mapped_column(String(40))  # claude | offline
    model: Mapped[str | None] = mapped_column(String(60))
    prompt_version: Mapped[str] = mapped_column(String(20))
    raw: Mapped[dict] = mapped_column(default=dict)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    request_id: Mapped[str | None] = mapped_column(String(80))


class Invoice(TenantMixin, Base):
    __tablename__ = "invoices"
    __table_args__ = (
        Index("ix_invoices_match", "firm_id", "client_gstin", "supplier_gstin", "number_norm"),
    )
    client_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"),
                                                 index=True)
    client_gstin: Mapped[str | None] = mapped_column(String(15), index=True)
    document_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("documents.id",
                                                                                         ondelete="SET NULL"))
    supplier_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("suppliers.id"), index=True)
    source: Mapped[str] = mapped_column(String(20), default="document")  # document | import | manual
    doc_type: Mapped[str] = mapped_column(String(20), default="invoice")
    number: Mapped[str] = mapped_column(String(60), default="")
    number_norm: Mapped[str] = mapped_column(String(60), default="")
    invoice_date: Mapped[date | None] = mapped_column(Date, index=True)
    period: Mapped[str | None] = mapped_column(String(6), index=True)  # MMYYYY of invoice date
    supplier_gstin: Mapped[str | None] = mapped_column(String(15), index=True)
    supplier_name: Mapped[str | None] = mapped_column(String(300))
    buyer_gstin: Mapped[str | None] = mapped_column(String(15))
    buyer_name: Mapped[str | None] = mapped_column(String(300))
    place_of_supply: Mapped[str | None] = mapped_column(String(2))
    reverse_charge: Mapped[bool] = mapped_column(Boolean, default=False)
    taxable_value: Mapped[Decimal] = mapped_column(Money, default=0)
    igst: Mapped[Decimal] = mapped_column(Money, default=0)
    cgst: Mapped[Decimal] = mapped_column(Money, default=0)
    sgst: Mapped[Decimal] = mapped_column(Money, default=0)
    cess: Mapped[Decimal] = mapped_column(Money, default=0)
    round_off: Mapped[Decimal] = mapped_column(Money, default=0)
    total: Mapped[Decimal] = mapped_column(Money, default=0)
    irn: Mapped[str | None] = mapped_column(String(80))
    qr_status: Mapped[str | None] = mapped_column(String(20))  # none|verified|mismatch|unreadable
    status: Mapped[str] = mapped_column(String(20), default="review", index=True)
    # review | approved | rejected | duplicate
    confidence: Mapped[float | None] = mapped_column(Float)
    blocked_amount: Mapped[Decimal] = mapped_column(Money, default=0)  # ITC blocked under 17(5)
    goods_received: Mapped[bool] = mapped_column(Boolean, default=True)
    received_on: Mapped[date | None] = mapped_column(Date)
    approved_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    claimed_period: Mapped[str | None] = mapped_column(String(6))  # period in which ITC was claimed in 3B
    notes: Mapped[str | None] = mapped_column(Text)
    flags: Mapped[list] = mapped_column(default=list)  # anomaly codes

    @property
    def itc(self) -> Decimal:
        return (self.igst or 0) + (self.cgst or 0) + (self.sgst or 0) + (self.cess or 0)


class InvoiceLine(TenantMixin, Base):
    __tablename__ = "invoice_lines"
    invoice_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("invoices.id", ondelete="CASCADE"),
                                                  index=True)
    position: Mapped[int] = mapped_column(Integer)
    description: Mapped[str] = mapped_column(Text, default="")
    hsn: Mapped[str | None] = mapped_column(String(10))
    quantity: Mapped[Decimal | None] = mapped_column(Money)
    unit: Mapped[str | None] = mapped_column(String(20))
    unit_price: Mapped[Decimal | None] = mapped_column(Money)
    taxable_value: Mapped[Decimal] = mapped_column(Money, default=0)
    tax_rate: Mapped[Decimal | None] = mapped_column(Money)
    igst: Mapped[Decimal] = mapped_column(Money, default=0)
    cgst: Mapped[Decimal] = mapped_column(Money, default=0)
    sgst: Mapped[Decimal] = mapped_column(Money, default=0)
    cess: Mapped[Decimal] = mapped_column(Money, default=0)
    blocked_reason: Mapped[str | None] = mapped_column(String(300))


class FieldEvidence(TenantMixin, Base):
    __tablename__ = "field_evidence"
    invoice_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("invoices.id", ondelete="CASCADE"),
                                                  index=True)
    field: Mapped[str] = mapped_column(String(60))
    value: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    page: Mapped[int | None] = mapped_column(Integer)
    token_ids: Mapped[list] = mapped_column(default=list)
    bbox: Mapped[list | None] = mapped_column()  # [x0,y0,x1,y1] normalised 0..1
    source: Mapped[str] = mapped_column(String(20), default="model")  # model|validator|memory|qr|human|import
    signals: Mapped[dict] = mapped_column(default=dict)


class ValidationResult(TenantMixin, Base):
    __tablename__ = "validation_results"
    invoice_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("invoices.id", ondelete="CASCADE"),
                                                  index=True)
    code: Mapped[str] = mapped_column(String(60))
    severity: Mapped[str] = mapped_column(String(10))
    message: Mapped[str] = mapped_column(Text)
    field: Mapped[str | None] = mapped_column(String(60))
    fix: Mapped[dict | None] = mapped_column()
    rule: Mapped[str | None] = mapped_column(String(60))
    data: Mapped[dict] = mapped_column(default=dict)
    resolved: Mapped[bool] = mapped_column(Boolean, default=False)


class SupplierMemory(TenantMixin, Base):
    """Learned per-supplier hints from human corrections (G7)."""

    __tablename__ = "supplier_memory"
    __table_args__ = (UniqueConstraint("firm_id", "supplier_gstin", "field", "wrong", name="uq_memory"),)
    supplier_gstin: Mapped[str] = mapped_column(String(15), index=True)
    field: Mapped[str] = mapped_column(String(60))
    wrong: Mapped[str] = mapped_column(String(300))
    right: Mapped[str] = mapped_column(String(300))
    hint: Mapped[str] = mapped_column(Text, default="")
    hits: Mapped[int] = mapped_column(Integer, default=1)


class Payment(TenantMixin, Base):
    __tablename__ = "payments"
    invoice_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("invoices.id", ondelete="CASCADE"),
                                                  index=True)
    amount: Mapped[Decimal] = mapped_column(Money)
    paid_on: Mapped[date] = mapped_column(Date)
    mode: Mapped[str | None] = mapped_column(String(30))
    reference: Mapped[str | None] = mapped_column(String(100))
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))


class Gstr2bImport(TenantMixin, Base):
    __tablename__ = "gstr2b_imports"
    __table_args__ = (Index("ix_2b_import_period", "firm_id", "client_gstin", "period"),)
    client_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"))
    client_gstin: Mapped[str] = mapped_column(String(15))
    period: Mapped[str] = mapped_column(String(6))  # MMYYYY
    generated_on: Mapped[date | None] = mapped_column(Date)
    filename: Mapped[str] = mapped_column(String(300))
    storage_key: Mapped[str | None] = mapped_column(String(400))
    record_count: Mapped[int] = mapped_column(Integer, default=0)
    summary: Mapped[dict] = mapped_column(default=dict)
    superseded: Mapped[bool] = mapped_column(Boolean, default=False)
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))


class Gstr2bRecord(TenantMixin, Base):
    __tablename__ = "gstr2b_records"
    __table_args__ = (Index("ix_2b_rec_match", "firm_id", "client_gstin", "supplier_gstin", "number_norm"),)
    import_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("gstr2b_imports.id",
                                                                                ondelete="CASCADE"), index=True)
    client_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"))
    client_gstin: Mapped[str] = mapped_column(String(15))
    period: Mapped[str] = mapped_column(String(6), index=True)
    section: Mapped[str] = mapped_column(String(10))  # b2b | b2ba | cdnr | cdnra | isd | impg
    doc_type: Mapped[str] = mapped_column(String(20), default="invoice")
    supplier_gstin: Mapped[str] = mapped_column(String(15), index=True)
    supplier_name: Mapped[str | None] = mapped_column(String(300))
    supplier_filing_date: Mapped[date | None] = mapped_column(Date)
    supplier_period: Mapped[str | None] = mapped_column(String(6))
    number: Mapped[str] = mapped_column(String(60))
    number_norm: Mapped[str] = mapped_column(String(60))
    invoice_date: Mapped[date | None] = mapped_column(Date)
    place_of_supply: Mapped[str | None] = mapped_column(String(2))
    reverse_charge: Mapped[bool] = mapped_column(Boolean, default=False)
    taxable_value: Mapped[Decimal] = mapped_column(Money, default=0)
    igst: Mapped[Decimal] = mapped_column(Money, default=0)
    cgst: Mapped[Decimal] = mapped_column(Money, default=0)
    sgst: Mapped[Decimal] = mapped_column(Money, default=0)
    cess: Mapped[Decimal] = mapped_column(Money, default=0)
    total: Mapped[Decimal] = mapped_column(Money, default=0)
    itc_available: Mapped[bool] = mapped_column(Boolean, default=True)
    itc_unavailable_reason: Mapped[str | None] = mapped_column(String(200))
    irn: Mapped[str | None] = mapped_column(String(80))
    original_number: Mapped[str | None] = mapped_column(String(60))  # for amendments
    raw: Mapped[dict] = mapped_column(default=dict)

    @property
    def itc(self) -> Decimal:
        return (self.igst or 0) + (self.cgst or 0) + (self.sgst or 0) + (self.cess or 0)


class Match(TenantMixin, Base):
    __tablename__ = "matches"
    client_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"),
                                                 index=True)
    client_gstin: Mapped[str] = mapped_column(String(15))
    period: Mapped[str] = mapped_column(String(6), index=True)  # 2B period
    invoice_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("invoices.id",
                                                                                        ondelete="CASCADE"),
                                                         index=True)
    record_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("gstr2b_records.id",
                                                                                       ondelete="CASCADE"),
                                                        index=True)
    group_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))  # for split/merge matches
    kind: Mapped[str] = mapped_column(String(20))  # exact|normalised|fuzzy|split|manual|unmatched
    status: Mapped[str] = mapped_column(String(30), index=True)
    # matched | mismatch | missing_in_2b | missing_in_books | duplicate
    mismatch_types: Mapped[list] = mapped_column(default=list)
    score: Mapped[float] = mapped_column(Float, default=0.0)
    features: Mapped[dict] = mapped_column(default=dict)
    explanation: Mapped[dict] = mapped_column(default=dict)
    itc_books: Mapped[Decimal] = mapped_column(Money, default=0)
    itc_2b: Mapped[Decimal] = mapped_column(Money, default=0)
    confirmed_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)


class ImsDecision(TenantMixin, Base):
    __tablename__ = "ims_decisions"
    __table_args__ = (UniqueConstraint("record_id"),)
    client_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"))
    record_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("gstr2b_records.id",
                                                                                ondelete="CASCADE"))
    period: Mapped[str] = mapped_column(String(6), index=True)
    recommendation: Mapped[str] = mapped_column(String(10))  # accept | reject | pending
    reason_codes: Mapped[list] = mapped_column(default=list)
    reason_text: Mapped[str] = mapped_column(Text, default="")
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    decided_action: Mapped[str | None] = mapped_column(String(10))
    decided_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    remark: Mapped[str | None] = mapped_column(Text)


class RiskItem(TenantMixin, Base):
    __tablename__ = "risk_items"
    __table_args__ = (Index("ix_risk_open", "firm_id", "client_id", "status"),)
    client_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"))
    client_gstin: Mapped[str | None] = mapped_column(String(15))
    invoice_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("invoices.id",
                                                                                        ondelete="CASCADE"))
    record_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("gstr2b_records.id",
                                                                                       ondelete="CASCADE"))
    supplier_gstin: Mapped[str | None] = mapped_column(String(15), index=True)
    kind: Mapped[str] = mapped_column(String(30), index=True)
    # R37_PAYMENT | R37A_SUPPLIER_3B | MSME_43BH | S16_4_DEADLINE | S17_5_BLOCKED | NOT_IN_2B | VALUE_MISMATCH
    # | WRONG_TAX_HEAD | NOT_RECEIVED | SUPPLIER_RISK
    severity: Mapped[str] = mapped_column(String(10), default="medium")  # low|medium|high|critical
    amount: Mapped[Decimal] = mapped_column(Money, default=0)  # ITC (or deduction, for 43B(h)) at risk
    interest: Mapped[Decimal] = mapped_column(Money, default=0)
    due_date: Mapped[date | None] = mapped_column(Date, index=True)
    title: Mapped[str] = mapped_column(String(300))
    detail: Mapped[str] = mapped_column(Text, default="")
    rule: Mapped[str | None] = mapped_column(String(60))
    status: Mapped[str] = mapped_column(String(20), default="open")  # open | resolved | accepted | reversed
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fingerprint: Mapped[str] = mapped_column(String(200), index=True)


class Forecast(TenantMixin, Base):
    __tablename__ = "forecasts"
    invoice_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("invoices.id", ondelete="CASCADE"),
                                                  index=True)
    supplier_gstin: Mapped[str] = mapped_column(String(15))
    p_next: Mapped[float] = mapped_column(Float)
    p_by_deadline: Mapped[float] = mapped_column(Float)
    p_never: Mapped[float] = mapped_column(Float)
    expected_lag: Mapped[float | None] = mapped_column(Float)
    recommendation: Mapped[str] = mapped_column(String(20))  # wait | chase | escalate | write_off
    explanation: Mapped[str] = mapped_column(Text, default="")
    model_version: Mapped[str] = mapped_column(String(20))
    as_of: Mapped[date] = mapped_column(Date)
