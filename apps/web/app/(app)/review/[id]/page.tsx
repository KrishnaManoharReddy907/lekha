"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, ArrowLeft, Check, ChevronLeft, ChevronRight, Info, QrCode, ShieldCheck, Sparkles, Wand2, X } from "lucide-react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import { DocViewer, type Highlight } from "@/components/doc-viewer";
import { Badge, Button, ConfidenceMeter, Input, Select, Spinner, StatusBadge, cx } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { useClients } from "@/lib/clients";
import { STATE_NAMES, date, inr } from "@/lib/format";
import { gstinProblem } from "@/lib/gstin";
import { useSession } from "@/lib/session";
import type { Evidence, InvoiceDetail, Line, Validation } from "@/lib/types";

type FieldKey =
  | "supplier_gstin" | "supplier_name" | "buyer_gstin" | "buyer_name" | "number" | "invoice_date" | "place_of_supply"
  | "doc_type" | "taxable_value" | "igst" | "cgst" | "sgst" | "cess" | "round_off" | "total" | "irn";

const GROUPS: { title: string; fields: { key: FieldKey; label: string; mono?: boolean; money?: boolean; kind?: "gstin" | "date" | "pos" | "doctype" }[] }[] = [
  {
    title: "Supplier",
    fields: [
      { key: "supplier_gstin", label: "GSTIN", mono: true, kind: "gstin" },
      { key: "supplier_name", label: "Name" },
    ],
  },
  {
    title: "Billed to",
    fields: [
      { key: "buyer_gstin", label: "GSTIN", mono: true, kind: "gstin" },
      { key: "buyer_name", label: "Name" },
    ],
  },
  {
    title: "Invoice",
    fields: [
      { key: "doc_type", label: "Document", kind: "doctype" },
      { key: "number", label: "Number", mono: true },
      { key: "invoice_date", label: "Date", kind: "date" },
      { key: "place_of_supply", label: "Place of supply", kind: "pos" },
      { key: "irn", label: "IRN (e-invoice)", mono: true },
    ],
  },
  {
    title: "Amounts (₹)",
    fields: [
      { key: "taxable_value", label: "Taxable value", money: true },
      { key: "igst", label: "IGST", money: true },
      { key: "cgst", label: "CGST", money: true },
      { key: "sgst", label: "SGST", money: true },
      { key: "cess", label: "Cess", money: true },
      { key: "round_off", label: "Round off", money: true },
      { key: "total", label: "Invoice total", money: true },
    ],
  },
];

const EVIDENCE_KEY: Partial<Record<FieldKey, string>> = { invoice_date: "date" };
const SOURCE_LABEL: Record<string, string> = { model: "AI", offline: "Rules", memory: "Memory", qr: "Signed QR", human: "You", import: "Import", validator: "Fix" };

export default function ReviewWorkstation() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const qc = useQueryClient();
  const { can } = useSession();
  const { current } = useClients();
  const [draft, setDraft] = useState<Partial<Record<FieldKey, string>>>({});
  const [lines, setLines] = useState<Line[] | null>(null);
  const [active, setActive] = useState<FieldKey | null>(null);

  const q = useQuery({ queryKey: ["invoice", id], queryFn: () => api<InvoiceDetail>(`/api/invoices/${id}`) });
  const queue = useQuery({
    queryKey: ["review-queue", current?.id],
    enabled: !!current,
    queryFn: () => api<{ id: string }[]>("/api/invoices/review-queue", { params: { client_id: current?.id } }),
  });

  useEffect(() => {
    setDraft({});
    setLines(null);
    setActive(null);
  }, [id]);

  const inv = q.data?.invoice;
  const value = useCallback(
    (k: FieldKey): string => {
      if (k in draft) return draft[k] ?? "";
      const v = inv?.[k as keyof typeof inv];
      return v === null || v === undefined ? "" : String(v);
    },
    [draft, inv],
  );
  const dirty = Object.keys(draft).length > 0 || lines !== null;
  const locked = inv?.status === "approved";

  const save = useMutation({
    mutationFn: () =>
      api<InvoiceDetail>(`/api/invoices/${id}`, {
        method: "PATCH",
        body: {
          fields: Object.fromEntries(Object.entries(draft).map(([k, v]) => [k, v === "" ? null : v])),
          lines: lines?.map(({ description, hsn, quantity, unit, unit_price, taxable_value, tax_rate, igst, cgst, sgst, cess }) => ({ description, hsn, quantity, unit, unit_price, taxable_value, tax_rate, igst, cgst, sgst, cess })),
        },
      }),
    onSuccess: (d) => {
      qc.setQueryData(["invoice", id], d);
      setDraft({});
      setLines(null);
      toast.success("Saved and re-checked");
    },
    onError: (e) => toast.error(errorMessage(e)),
  });

  const status = useMutation({
    mutationFn: async (s: "approved" | "rejected" | "review") => {
      if (dirty) await save.mutateAsync();
      return api(`/api/invoices/${id}/status`, { body: { status: s } });
    },
    onSuccess: (_d, s) => {
      qc.invalidateQueries({ queryKey: ["review-queue"] });
      qc.invalidateQueries({ queryKey: ["documents"] });
      qc.invalidateQueries({ queryKey: ["invoice", id] });
      if (s === "review") return;
      toast.success(s === "approved" ? "Approved" : "Rejected");
      const rest = (queue.data ?? []).filter((x) => x.id !== id);
      router.push(rest.length ? `/review/${rest[0].id}` : "/review");
    },
    onError: (e) => toast.error(errorMessage(e)),
  });

  const fix = useMutation({
    mutationFn: (vid: string) => api<InvoiceDetail>(`/api/invoices/${id}/apply-fix/${vid}`, { method: "POST" }),
    onSuccess: (d) => {
      qc.setQueryData(["invoice", id], d);
      toast.success("Fix applied");
    },
    onError: (e) => toast.error(errorMessage(e)),
  });

  const pos = useMemo(() => (queue.data ?? []).findIndex((x) => x.id === id), [queue.data, id]);
  const prevId = pos > 0 ? queue.data?.[pos - 1].id : undefined;
  const nextId = pos >= 0 && queue.data && pos < queue.data.length - 1 ? queue.data[pos + 1].id : undefined;

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      const typing = (e.target as HTMLElement)?.closest("input,textarea,select");
      if ((e.ctrlKey || e.metaKey) && e.key === "s") {
        e.preventDefault();
        if (dirty && !locked) save.mutate();
        return;
      }
      if (typing) return;
      if (e.key === "a" && can("write") && !locked) status.mutate("approved");
      if (e.key === "j" && nextId) router.push(`/review/${nextId}`);
      if (e.key === "k" && prevId) router.push(`/review/${prevId}`);
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [dirty, locked, nextId, prevId, can, router, save, status]);

  if (q.isLoading || !q.data || !inv)
    return (
      <div className="grid h-64 place-items-center">
        {q.isError ? <p className="text-red">{errorMessage(q.error)}</p> : <Spinner className="size-6" />}
      </div>
    );

  const d = q.data;
  const ev = (k: FieldKey): Evidence | undefined => d.evidence[EVIDENCE_KEY[k] ?? k];
  const fieldIssues = (k: FieldKey) => d.validations.filter((v) => v.field && (v.field === k || v.field === EVIDENCE_KEY[k] || v.field.startsWith(`${k}.`)));
  const activeEv = active ? ev(active) : undefined;
  const activeIssue = active ? fieldIssues(active)[0] : undefined;
  const highlight: Highlight | null =
    activeEv?.bbox && activeEv.page ? { page: activeEv.page, bbox: activeEv.bbox, tone: activeIssue?.severity === "error" ? "bad" : activeIssue ? "warn" : "accent" } : null;
  const errors = d.validations.filter((v) => v.severity === "error");
  const warnings = d.validations.filter((v) => v.severity === "warning");
  const infos = d.validations.filter((v) => v.severity === "info");
  const qr = d.document?.qr as { kind?: string; payload?: Record<string, unknown> } | null | undefined;
  const pickLabel = active ? GROUPS.flatMap((g) => g.fields).find((f) => f.key === active)?.label ?? null : null;
  const editable = can("write") && !locked;

  return (
    <div className="-mx-[max(16px,env(safe-area-inset-left))] -my-6 lg:-mx-8">
      {/* Top bar */}
      <div className="sticky top-[env(safe-area-inset-top)] z-10 flex flex-wrap items-center gap-3 border-b border-rule bg-sheet px-4 py-2.5 lg:top-0">
        <Link href="/inbox" className="rounded p-1 text-ink-3 hover:bg-sunk" aria-label="Back to inbox">
          <ArrowLeft className="size-4" />
        </Link>
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <h1 className="truncate font-display text-[16px] font-semibold">{inv.supplier_name || "Unknown supplier"}</h1>
            <StatusBadge status={inv.status} />
            {inv.qr_status === "verified" && (
              <Badge tone="ok" title="The government-signed e-invoice QR matches the printed invoice">
                <QrCode className="size-3" /> QR verified
              </Badge>
            )}
            {inv.qr_status === "mismatch" && <Badge tone="bad"><QrCode className="size-3" /> QR mismatch</Badge>}
          </div>
          <div className="num text-[12.5px] text-ink-3">
            {inv.number || "no number"} · {date(inv.invoice_date)} · ITC {inr(inv.itc)}
          </div>
        </div>
        <div className="ml-auto flex items-center gap-2">
          <span className="hidden text-[12px] text-ink-3 sm:inline">Confidence</span>
          <ConfidenceMeter value={inv.confidence} />
          {queue.data && pos >= 0 && (
            <div className="flex items-center rounded-md border border-rule">
              <button disabled={!prevId} onClick={() => prevId && router.push(`/review/${prevId}`)} className="p-1.5 text-ink-2 disabled:opacity-30" aria-label="Previous (K)" title="Previous (K)">
                <ChevronLeft className="size-4" />
              </button>
              <span className="num px-1 text-[12px] text-ink-3">
                {pos + 1}/{queue.data.length}
              </span>
              <button disabled={!nextId} onClick={() => nextId && router.push(`/review/${nextId}`)} className="p-1.5 text-ink-2 disabled:opacity-30" aria-label="Next (J)" title="Next (J)">
                <ChevronRight className="size-4" />
              </button>
            </div>
          )}
          {editable && (
            <>
              <Button size="sm" variant="ghost" icon={<X className="size-4" />} onClick={() => status.mutate("rejected")} loading={status.isPending && status.variables === "rejected"}>
                Reject
              </Button>
              {dirty && (
                <Button size="sm" onClick={() => save.mutate()} loading={save.isPending}>
                  Save
                </Button>
              )}
              <Button size="sm" variant="primary" icon={<Check className="size-4" />} onClick={() => status.mutate("approved")} loading={status.isPending && status.variables === "approved"} title="Approve (A)">
                Approve
              </Button>
            </>
          )}
          {locked && can("write") && (
            <Button size="sm" onClick={() => status.mutate("review")}>
              Reopen
            </Button>
          )}
        </div>
      </div>

      <div className="grid lg:h-[calc(100dvh-57px)] lg:grid-cols-[minmax(0,1.15fr)_minmax(380px,1fr)]">
        {/* Document */}
        <div className="h-[60dvh] border-b border-rule lg:h-auto lg:border-r lg:border-b-0">
          {d.document ? (
            <DocViewer
              pages={d.document.page_urls}
              tokens={(d.document.ocr?.pages ?? []).map((p) => p.tokens)}
              highlight={highlight}
              pickLabel={editable ? pickLabel : null}
              onPick={editable && active ? (t) => setDraft((x) => ({ ...x, [active]: cleanPick(active, t) })) : undefined}
            />
          ) : (
            <div className="grid h-full place-items-center text-ink-3">Entered manually — no document.</div>
          )}
        </div>

        {/* Fields */}
        <div className="overflow-y-auto bg-paper p-4 lg:p-5">
          {(errors.length > 0 || warnings.length > 0 || inv.flags?.length > 0) && (
            <section className="mb-5 flex flex-col gap-2" aria-label="Checks">
              {errors.map((v) => <Issue key={v.id} v={v} onFix={editable ? () => fix.mutate(v.id) : undefined} onFocus={() => focusIssue(v, setActive)} />)}
              {inv.flags?.map((f) => (
                <div key={f.code} className="flex gap-2.5 rounded-md border border-amber/30 bg-amber-soft px-3 py-2 text-[13px] text-ink">
                  <AlertTriangle className="mt-0.5 size-4 shrink-0 text-amber" />
                  <span>
                    <span className="font-medium">Fraud & anomaly check: </span>
                    {f.message}
                  </span>
                </div>
              ))}
              {warnings.map((v) => <Issue key={v.id} v={v} onFix={editable ? () => fix.mutate(v.id) : undefined} onFocus={() => focusIssue(v, setActive)} />)}
            </section>
          )}
          {errors.length === 0 && warnings.length === 0 && (
            <div className="mb-5 flex items-center gap-2 rounded-md border border-ok/30 bg-ok-soft px-3 py-2 text-[13px] text-ok">
              <ShieldCheck className="size-4" /> All GST checks passed.
            </div>
          )}

          {GROUPS.map((g) => (
            <section key={g.title} className="mb-5">
              <h2 className="mb-2 text-[12px] font-semibold tracking-wider text-ink-3 uppercase">{g.title}</h2>
              <div className="rounded-lg border border-rule bg-sheet">
                {g.fields.map((f) => {
                  const e = ev(f.key);
                  const issues = fieldIssues(f.key);
                  const sev = issues.find((i) => i.severity === "error") ? "error" : issues.length ? "warning" : null;
                  const v = value(f.key);
                  const changed = f.key in draft;
                  const gstErr = f.kind === "gstin" && v ? gstinProblem(v) : null;
                  return (
                    <div
                      key={f.key}
                      className={cx("grid grid-cols-[120px_1fr_auto] items-center gap-3 border-b border-rule px-3 py-2 last:border-b-0", active === f.key && "bg-accent-soft/50")}
                    >
                      <label htmlFor={`f-${f.key}`} className="text-[13px] text-ink-2">
                        {f.label}
                      </label>
                      <div>
                        <FieldInput
                          id={`f-${f.key}`}
                          f={f}
                          value={v}
                          disabled={!editable}
                          invalid={sev === "error" || !!gstErr}
                          onFocus={() => setActive(f.key)}
                          onChange={(nv) => setDraft((x) => ({ ...x, [f.key]: nv }))}
                        />
                        {gstErr && <p className="mt-1 text-[12px] text-red">{gstErr}</p>}
                        {f.kind === "pos" && v && STATE_NAMES[v] && <p className="mt-0.5 text-[12px] text-ink-3">{STATE_NAMES[v]}</p>}
                      </div>
                      <div className="flex w-[74px] flex-col items-end gap-0.5">
                        {changed ? (
                          <Badge tone="info">Edited</Badge>
                        ) : e ? (
                          <>
                            <Dot confidence={e.confidence} />
                            <span className="text-[11px] text-ink-3">{SOURCE_LABEL[e.source] ?? e.source}</span>
                          </>
                        ) : null}
                      </div>
                    </div>
                  );
                })}
              </div>
            </section>
          ))}

          <LinesEditor lines={lines ?? d.lines} editable={editable} onChange={setLines} />

          {infos.length > 0 && (
            <section className="mb-5 flex flex-col gap-1.5">
              {infos.map((v) => (
                <p key={v.id} className="flex gap-2 text-[12.5px] text-ink-3">
                  <Info className="mt-0.5 size-3.5 shrink-0" /> {v.message}
                </p>
              ))}
            </section>
          )}

          {qr?.kind === "einvoice" && qr.payload && (
            <section className="mb-5 rounded-lg border border-rule bg-sheet p-3">
              <h2 className="mb-2 flex items-center gap-1.5 text-[12px] font-semibold tracking-wider text-ink-3 uppercase">
                <QrCode className="size-3.5" /> Signed e-invoice QR
              </h2>
              <dl className="grid grid-cols-[110px_1fr] gap-x-3 gap-y-1 text-[12.5px]">
                {Object.entries(qr.payload).map(([k, val]) => (
                  <div key={k} className="contents">
                    <dt className="text-ink-3">{k}</dt>
                    <dd className="num break-all">{String(val)}</dd>
                  </div>
                ))}
              </dl>
            </section>
          )}

          <section className="mb-2 text-[12px] text-ink-3">
            {d.extraction && (
              <p className="flex items-center gap-1.5">
                <Sparkles className="size-3.5" />
                Read by {d.extraction.engine === "claude" ? `AI (${d.extraction.model})` : "offline rules"}
                {d.extraction.engine === "claude" && ` · ${(d.extraction.latency_ms / 1000).toFixed(1)}s · $${d.extraction.cost_usd.toFixed(3)}`}
              </p>
            )}
            {d.extraction?.notes && <p className="mt-1">Note: {d.extraction.notes}</p>}
            <p className="mt-2">
              Shortcuts: <kbd className="num">A</kbd> approve · <kbd className="num">J/K</kbd> next/previous · <kbd className="num">Ctrl+S</kbd> save
            </p>
          </section>
        </div>
      </div>
    </div>
  );
}

function focusIssue(v: Validation, setActive: (k: FieldKey) => void) {
  if (!v.field) return;
  const root = v.field.split(".")[0].split("[")[0];
  const key = (root === "date" ? "invoice_date" : root) as FieldKey;
  setActive(key);
  document.getElementById(`f-${key}`)?.focus();
}

function cleanPick(field: FieldKey, text: string): string {
  const t = text.trim();
  if (["taxable_value", "igst", "cgst", "sgst", "cess", "round_off", "total"].includes(field)) {
    const m = t.replace(/,/g, "").match(/-?\d+(\.\d+)?/);
    return m ? m[0] : t;
  }
  if (field === "supplier_gstin" || field === "buyer_gstin") {
    const m = t.toUpperCase().replace(/\s/g, "").match(/\d{2}[A-Z0-9]{13}/);
    return m ? m[0] : t;
  }
  if (field === "number") return t.replace(/^(invoice|inv|bill)\s*(no\.?|number|#)\s*[:\-]?\s*/i, "");
  return t;
}

function Dot({ confidence }: { confidence: number }) {
  const tone = confidence >= 0.9 ? "bg-ok" : confidence >= 0.7 ? "bg-amber" : "bg-red";
  return (
    <span className="flex items-center gap-1" title={`${Math.round(confidence * 100)}% confident`}>
      <span className={cx("size-2 rounded-full", tone)} />
      <span className="num text-[11px] text-ink-2">{Math.round(confidence * 100)}%</span>
    </span>
  );
}

function FieldInput({
  id, f, value, disabled, invalid, onFocus, onChange,
}: {
  id: string;
  f: { key: FieldKey; mono?: boolean; money?: boolean; kind?: string };
  value: string;
  disabled: boolean;
  invalid: boolean;
  onFocus: () => void;
  onChange: (v: string) => void;
}) {
  if (f.kind === "doctype")
    return (
      <Select id={id} value={value || "invoice"} disabled={disabled} onFocus={onFocus} onChange={(e) => onChange(e.target.value)} className="h-8">
        <option value="invoice">Tax invoice</option>
        <option value="credit_note">Credit note</option>
        <option value="debit_note">Debit note</option>
        <option value="bill_of_supply">Bill of supply (no GST)</option>
      </Select>
    );
  if (f.kind === "pos")
    return (
      <Select id={id} value={value} disabled={disabled} onFocus={onFocus} onChange={(e) => onChange(e.target.value)} className="h-8">
        <option value="">—</option>
        {Object.entries(STATE_NAMES).map(([c, n]) => (
          <option key={c} value={c}>
            {c} · {n}
          </option>
        ))}
      </Select>
    );
  return (
    <Input
      id={id}
      type={f.kind === "date" ? "date" : "text"}
      inputMode={f.money ? "decimal" : undefined}
      value={value}
      disabled={disabled}
      invalid={invalid}
      onFocus={onFocus}
      onChange={(e) => onChange(f.kind === "gstin" ? e.target.value.toUpperCase() : e.target.value)}
      className={cx("h-8", (f.mono || f.money) && "num", f.money && "text-right")}
    />
  );
}

function Issue({ v, onFix, onFocus }: { v: Validation; onFix?: () => void; onFocus: () => void }) {
  const bad = v.severity === "error";
  return (
    <div className={cx("flex items-start gap-2.5 rounded-md border px-3 py-2 text-[13px]", bad ? "border-red/30 bg-red-soft" : "border-amber/30 bg-amber-soft")}>
      <AlertTriangle className={cx("mt-0.5 size-4 shrink-0", bad ? "text-red" : "text-amber")} />
      <button type="button" onClick={onFocus} className="flex-1 text-left text-ink">
        {v.message}
        {v.rule && <span className="ml-1.5 text-[11px] text-ink-3">({v.rule})</span>}
      </button>
      {v.fix && onFix && (
        <Button size="sm" variant="secondary" icon={<Wand2 className="size-3.5" />} onClick={onFix} title={String(v.fix.reason ?? "")}>
          Fix
        </Button>
      )}
    </div>
  );
}

function LinesEditor({ lines, editable, onChange }: { lines: Line[]; editable: boolean; onChange: (l: Line[]) => void }) {
  const set = (i: number, k: keyof Line, v: string) => onChange(lines.map((ln, j) => (j === i ? { ...ln, [k]: v } : ln)));
  return (
    <section className="mb-5">
      <div className="mb-2 flex items-center justify-between">
        <h2 className="text-[12px] font-semibold tracking-wider text-ink-3 uppercase">Line items</h2>
        {editable && (
          <button
            className="text-[12.5px] text-accent"
            onClick={() =>
              onChange([...lines, { id: `new-${lines.length}`, position: lines.length, description: "", hsn: "", quantity: null, unit: null, unit_price: null, taxable_value: "0", tax_rate: "18", igst: "0", cgst: "0", sgst: "0", cess: "0", blocked_reason: null }])
            }
          >
            + Add line
          </button>
        )}
      </div>
      {lines.length === 0 ? (
        <p className="rounded-lg border border-dashed border-rule-2 px-3 py-4 text-center text-[12.5px] text-ink-3">No line items read.</p>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-rule bg-sheet">
          <table className="w-full min-w-[520px] text-[12.5px]">
            <thead>
              <tr className="bg-sunk text-left text-[11.5px] text-ink-2">
                <th className="px-2 py-1.5 font-semibold">Description</th>
                <th className="px-2 py-1.5 font-semibold">HSN</th>
                <th className="px-2 py-1.5 text-right font-semibold">Taxable</th>
                <th className="px-2 py-1.5 text-right font-semibold">GST %</th>
                {editable && <th />}
              </tr>
            </thead>
            <tbody>
              {lines.map((ln, i) => (
                <tr key={ln.id} className="border-t border-rule">
                  <td className="px-2 py-1">
                    <input disabled={!editable} className="w-full bg-transparent outline-none" value={ln.description} onChange={(e) => set(i, "description", e.target.value)} aria-label="Description" />
                    {ln.blocked_reason && <Badge tone="warn" className="mt-0.5" title={ln.blocked_reason}>Blocked credit 17(5)</Badge>}
                  </td>
                  <td className="px-2 py-1">
                    <input disabled={!editable} className="num w-16 bg-transparent outline-none" value={ln.hsn ?? ""} onChange={(e) => set(i, "hsn", e.target.value)} aria-label="HSN" />
                  </td>
                  <td className="px-2 py-1 text-right">
                    <input disabled={!editable} className="num w-24 bg-transparent text-right outline-none" value={ln.taxable_value} onChange={(e) => set(i, "taxable_value", e.target.value)} aria-label="Taxable value" />
                  </td>
                  <td className="px-2 py-1 text-right">
                    <input disabled={!editable} className="num w-12 bg-transparent text-right outline-none" value={ln.tax_rate ?? ""} onChange={(e) => set(i, "tax_rate", e.target.value)} aria-label="Tax rate" />
                  </td>
                  {editable && (
                    <td className="px-1">
                      <button className="text-ink-3 hover:text-red" onClick={() => onChange(lines.filter((_, j) => j !== i))} aria-label="Remove line">
                        <X className="size-3.5" />
                      </button>
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
