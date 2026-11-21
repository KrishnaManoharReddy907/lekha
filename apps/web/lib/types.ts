export type Role = "owner" | "partner" | "staff" | "client" | "viewer";

export interface Me {
  user: { id: string; name: string; email: string; language: string };
  firm: { id: string; name: string; kind: string; role: Role; is_demo: boolean } | null;
  firms: { id: string; name: string; kind: string; role: Role; is_demo: boolean }[];
  csrf_token: string;
}

export interface Gstin {
  id: string;
  gstin: string;
  state_code: string;
  legal_name: string | null;
  is_primary: boolean;
}

export interface Client {
  id: string;
  name: string;
  trade_name: string | null;
  contact_name: string | null;
  contact_email: string | null;
  contact_phone: string | null;
  language: string;
  filing_frequency: string;
  archived: boolean;
  gstins: Gstin[];
}

export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export interface DocumentRow {
  id: string;
  client_id: string;
  filename: string;
  mime: string;
  size: number;
  status: "queued" | "processing" | "extracted" | "failed" | "duplicate";
  source: string;
  error: string | null;
  created_at: string;
  invoice: { id: string; status: string; number: string; supplier_name: string | null; total: string; confidence: number | null } | null;
}

export interface Invoice {
  id: string;
  client_id: string;
  client_gstin: string | null;
  document_id: string | null;
  supplier_id: string | null;
  source: string;
  doc_type: string;
  number: string;
  invoice_date: string | null;
  period: string | null;
  supplier_gstin: string | null;
  supplier_name: string | null;
  buyer_gstin: string | null;
  buyer_name: string | null;
  place_of_supply: string | null;
  reverse_charge: boolean;
  taxable_value: string;
  igst: string;
  cgst: string;
  sgst: string;
  cess: string;
  round_off: string;
  total: string;
  itc: string;
  irn: string | null;
  qr_status: string | null;
  status: "review" | "approved" | "rejected" | "duplicate";
  confidence: number | null;
  blocked_amount: string;
  goods_received: boolean;
  received_on: string | null;
  notes: string | null;
  flags: { code: string; severity: string; message: string; related?: string }[];
  created_at: string;
}

export interface Line {
  id: string;
  position: number;
  description: string;
  hsn: string | null;
  quantity: string | null;
  unit: string | null;
  unit_price: string | null;
  taxable_value: string;
  tax_rate: string | null;
  igst: string;
  cgst: string;
  sgst: string;
  cess: string;
  blocked_reason: string | null;
}

export interface Evidence {
  field: string;
  value: string | null;
  confidence: number;
  page: number | null;
  token_ids: number[];
  bbox: [number, number, number, number] | null;
  source: string;
  signals: Record<string, number>;
}

export interface Validation {
  id: string;
  code: string;
  severity: "error" | "warning" | "info";
  message: string;
  field: string | null;
  fix: Record<string, string> | null;
  rule: string | null;
  data: Record<string, unknown>;
}

export interface OcrToken {
  id: number;
  text: string;
  box: [number, number, number, number];
  conf: number;
}

export interface InvoiceDetail {
  invoice: Invoice;
  lines: Line[];
  evidence: Record<string, Evidence>;
  validations: Validation[];
  payments: { id: string; amount: string; paid_on: string; mode: string | null; reference: string | null }[];
  matches: Record<string, unknown>[];
  risks: Record<string, unknown>[];
  trail: { id: string; action: string; actor_kind: string; created_at: string; meta: Record<string, unknown>; before: Record<string, unknown> | null; after: Record<string, unknown> | null }[];
  document: (DocumentRow & { page_urls: string[]; original_url: string; pages: number; qr: Record<string, unknown> | null; ocr: { pages: { w: number; h: number; source: string; tokens: OcrToken[] }[] } | null }) | null;
  extraction: { engine: string; model: string | null; cost_usd: number; latency_ms: number; notes: string | null } | null;
}
