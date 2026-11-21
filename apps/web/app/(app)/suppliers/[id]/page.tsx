"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Copy, Mail, MessageCircle, Send } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";
import { Badge, Button, Card, CardHeader, Field, Input, PageHeader, Select, Spinner, StatusBadge, Table, Td, Textarea, Th } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { useClients } from "@/lib/clients";
import { date, inr, relTime } from "@/lib/format";
import { RISK_LABEL } from "@/lib/gst";
import { useSession } from "@/lib/session";
import { Grade, type SupplierRow } from "@/components/supplier-grade";

interface FollowUp { id: string; channel: string; language: string; subject: string; body: string; status: string; amount: string; created_at: string; sent_at: string | null; escalation_level: number }
interface Detail {
  supplier: SupplierRow & { email: string | null; phone: string | null; language: string; udyam_number: string | null; has_payment_agreement: boolean; notes: string | null };
  invoices: { id: string; number: string; invoice_date: string | null; client_name: string; itc: string; status: string }[];
  clients: string[];
  issues: { risk_id: string; kind: string; number: string; date: string | null; amount: string; client_id: string }[];
  followups: FollowUp[];
  responses: { action: string; message: string; expected_by: string | null; created_at: string }[];
}

export default function SupplierDetail() {
  const { id } = useParams<{ id: string }>();
  const qc = useQueryClient();
  const { can } = useSession();
  const { current } = useClients();
  const q = useQuery({ queryKey: ["supplier", id], queryFn: () => api<Detail>(`/api/suppliers/${id}`) });
  const [lang, setLang] = useState("");
  const [draft, setDraft] = useState<FollowUp | null>(null);

  const patch = useMutation({
    mutationFn: (body: Record<string, unknown>) => api(`/api/suppliers/${id}`, { method: "PATCH", body }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["supplier", id] }); toast.success("Saved — risk will be re-checked"); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const createFu = useMutation({
    mutationFn: () => api<FollowUp>(`/api/suppliers/${id}/followups`, { body: { client_id: current?.id, channel: "email", language: lang || undefined } }),
    onSuccess: (f) => { setDraft(f); qc.invalidateQueries({ queryKey: ["supplier", id] }); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const sendFu = useMutation({
    mutationFn: (f: FollowUp) => api<FollowUp>(`/api/followups/${f.id}`, { method: "PATCH", body: { subject: f.subject, body: f.body, status: "sent" } }),
    onSuccess: () => { toast.success("Marked as sent"); setDraft(null); qc.invalidateQueries({ queryKey: ["supplier", id] }); },
    onError: (e) => toast.error(errorMessage(e)),
  });

  if (!q.data) return <div className="grid h-64 place-items-center"><Spinner className="size-6" /></div>;
  const { supplier: s, issues, invoices, followups, responses } = q.data;
  const d = s.score_detail ?? {};
  const phone = (s.phone ?? "").replace(/\D/g, "");

  return (
    <>
      <Link href="/suppliers" className="mb-3 inline-flex items-center gap-1 text-[13px] text-ink-3 hover:text-ink"><ArrowLeft className="size-3.5" /> Suppliers</Link>
      <PageHeader
        title={s.trade_name || s.legal_name || s.gstin}
        sub={<span className="num">{s.gstin}</span>}
        actions={<Grade score={s.score} grade={d.grade} />}
      />
      <div className="mb-5 grid gap-3 sm:grid-cols-4">
        {[
          ["On time", d.on_time_rate !== undefined ? `${Math.round(d.on_time_rate * 100)}%` : "—"],
          ["Mismatch rate", d.mismatch_rate !== undefined ? `${Math.round(d.mismatch_rate * 100)}%` : "—"],
          ["Avg delay when late", d.avg_lag_months ? `${d.avg_lag_months} months` : "—"],
          ["Clients buying", String(q.data.clients.length)],
        ].map(([l, v]) => (
          <div key={l} className="rounded-lg border border-rule bg-sheet p-4"><div className="text-[12px] text-ink-3 uppercase">{l}</div><div className="num mt-1 text-[20px] font-semibold">{v}</div></div>
        ))}
      </div>
      <div className="grid gap-5 lg:grid-cols-[1.3fr_1fr]">
        <div className="flex flex-col gap-5">
          <Card>
            <CardHeader title="Open GST issues" sub="Things this supplier must fix for your clients to get their credit." />
            {issues.length === 0 ? <p className="text-[13px] text-ink-3">No open issues.</p> : (
              <ul className="divide-y divide-rule">
                {issues.map((i) => (
                  <li key={i.risk_id} className="flex items-center justify-between gap-3 py-2 text-[13px]">
                    <span><Badge tone="warn">{RISK_LABEL[i.kind] ?? i.kind}</Badge> <span className="num ml-1">{i.number}</span> <span className="text-ink-3">· {date(i.date)}</span></span>
                    <span className="num">{inr(i.amount)}</span>
                  </li>
                ))}
              </ul>
            )}
            {can("write") && issues.length > 0 && (
              <div className="mt-4 flex flex-wrap items-end gap-2 border-t border-rule pt-4">
                <Field label="Message language" htmlFor="fu-lang">
                  <Select id="fu-lang" value={lang} onChange={(e) => setLang(e.target.value)} className="w-40">
                    <option value="">Supplier default ({s.language})</option>
                    <option value="en">English</option>
                    <option value="hi">Hindi</option>
                    <option value="te">Telugu</option>
                  </Select>
                </Field>
                <Button variant="primary" icon={<Mail className="size-4" />} loading={createFu.isPending} onClick={() => createFu.mutate()}>Draft follow-up for {current?.name}</Button>
              </div>
            )}
          </Card>
          {draft && (
            <Card>
              <CardHeader title={draft.escalation_level ? `Escalation #${draft.escalation_level}` : "Follow-up draft"} sub="Includes a private link where the supplier can see the issues and reply." />
              <div className="flex flex-col gap-3">
                <Input aria-label="Subject" value={draft.subject} onChange={(e) => setDraft({ ...draft, subject: e.target.value })} />
                <Textarea aria-label="Message" rows={12} value={draft.body} onChange={(e) => setDraft({ ...draft, body: e.target.value })} />
                <div className="flex flex-wrap gap-2">
                  <Button icon={<Copy className="size-4" />} onClick={() => navigator.clipboard.writeText(draft.body).then(() => toast.success("Copied"))}>Copy text</Button>
                  <a className="inline-flex h-9 items-center gap-1.5 rounded-md border border-rule-2 bg-sheet px-3.5 text-sm font-medium hover:bg-sunk" target="_blank" rel="noreferrer"
                     href={`https://wa.me/${phone}?text=${encodeURIComponent(draft.body)}`}>
                    <MessageCircle className="size-4" /> Open in WhatsApp
                  </a>
                  {s.email && <a className="inline-flex h-9 items-center gap-1.5 rounded-md border border-rule-2 bg-sheet px-3.5 text-sm font-medium hover:bg-sunk" href={`mailto:${s.email}?subject=${encodeURIComponent(draft.subject)}&body=${encodeURIComponent(draft.body)}`}><Mail className="size-4" /> Open in email</a>}
                  <Button variant="primary" icon={<Send className="size-4" />} loading={sendFu.isPending} onClick={() => sendFu.mutate(draft)}>Mark as sent</Button>
                </div>
              </div>
            </Card>
          )}
          <Card>
            <CardHeader title="Invoices across your clients" />
            <Table>
              <thead><tr><Th>Date</Th><Th>Number</Th><Th>Client</Th><Th right>ITC</Th><Th>Status</Th></tr></thead>
              <tbody>
                {invoices.slice(0, 50).map((i) => (
                  <tr key={i.id}>
                    <Td>{date(i.invoice_date)}</Td>
                    <Td mono><Link href={`/review/${i.id}`} className="text-accent hover:underline">{i.number}</Link></Td>
                    <Td className="text-ink-2">{i.client_name}</Td>
                    <Td right mono>{inr(i.itc)}</Td>
                    <Td><StatusBadge status={i.status} /></Td>
                  </tr>
                ))}
              </tbody>
            </Table>
          </Card>
        </div>
        <div className="flex flex-col gap-5">
          <Card>
            <CardHeader title="Profile" sub="Used for MSME payment rules and messages." />
            <SupplierForm s={s} disabled={!can("write")} onSave={(b) => patch.mutate(b)} saving={patch.isPending} />
          </Card>
          <Card>
            <CardHeader title="Replies from supplier" />
            {responses.length === 0 ? <p className="text-[13px] text-ink-3">No replies yet.</p> : (
              <ul className="flex flex-col gap-2">
                {responses.map((r, i) => (
                  <li key={i} className="rounded-md bg-sunk p-2.5 text-[13px]">
                    <Badge tone="info">{r.action.replace("_", " ")}</Badge> <span className="text-ink-3">{relTime(r.created_at)}</span>
                    {r.message && <p className="mt-1">{r.message}</p>}
                    {r.expected_by && <p className="mt-0.5 text-[12px] text-ink-3">Expected by {date(r.expected_by)}</p>}
                  </li>
                ))}
              </ul>
            )}
          </Card>
          <Card>
            <CardHeader title="Follow-up history" />
            {followups.length === 0 ? <p className="text-[13px] text-ink-3">None yet.</p> : (
              <ul className="divide-y divide-rule">
                {followups.map((f) => (
                  <li key={f.id} className="flex items-center justify-between gap-2 py-2 text-[13px]">
                    <span className="min-w-0 truncate">{f.subject}</span>
                    <span className="flex shrink-0 items-center gap-2"><StatusBadge status={f.status} /><span className="text-[12px] text-ink-3">{relTime(f.sent_at ?? f.created_at)}</span></span>
                  </li>
                ))}
              </ul>
            )}
          </Card>
        </div>
      </div>
    </>
  );
}

function SupplierForm({ s, disabled, onSave, saving }: { s: Detail["supplier"]; disabled: boolean; onSave: (b: Record<string, unknown>) => void; saving: boolean }) {
  const [f, setF] = useState({ email: s.email ?? "", phone: s.phone ?? "", language: s.language, msme_category: s.msme_category ?? "none", udyam_number: s.udyam_number ?? "", has_payment_agreement: s.has_payment_agreement, gstr3b: "" });
  return (
    <form className="flex flex-col gap-3" onSubmit={(e) => { e.preventDefault(); onSave({ email: f.email || null, phone: f.phone || null, language: f.language, msme_category: f.msme_category, udyam_number: f.udyam_number || null, has_payment_agreement: f.has_payment_agreement, ...(f.gstr3b ? { gstr3b_filed: f.gstr3b === "yes" } : {}) }); }}>
      <Field label="Email" htmlFor="s-email"><Input id="s-email" type="email" disabled={disabled} value={f.email} onChange={(e) => setF({ ...f, email: e.target.value })} /></Field>
      <Field label="WhatsApp / phone" htmlFor="s-phone"><Input id="s-phone" disabled={disabled} value={f.phone} onChange={(e) => setF({ ...f, phone: e.target.value })} placeholder="+91 98xxxxxxxx" /></Field>
      <div className="grid grid-cols-2 gap-3">
        <Field label="Language" htmlFor="s-lang"><Select id="s-lang" disabled={disabled} value={f.language} onChange={(e) => setF({ ...f, language: e.target.value })}><option value="en">English</option><option value="hi">Hindi</option><option value="te">Telugu</option></Select></Field>
        <Field label="MSME (Udyam)" htmlFor="s-msme"><Select id="s-msme" disabled={disabled} value={f.msme_category} onChange={(e) => setF({ ...f, msme_category: e.target.value })}><option value="none">Not MSME</option><option value="micro">Micro</option><option value="small">Small</option><option value="medium">Medium</option></Select></Field>
      </div>
      <Field label="Udyam number" htmlFor="s-udyam"><Input id="s-udyam" disabled={disabled} value={f.udyam_number} onChange={(e) => setF({ ...f, udyam_number: e.target.value })} /></Field>
      <label className="flex items-center gap-2 text-[13px]"><input type="checkbox" disabled={disabled} checked={f.has_payment_agreement} onChange={(e) => setF({ ...f, has_payment_agreement: e.target.checked })} /> Written payment agreement (45 days instead of 15 for MSME)</label>
      <Field label="Has the supplier filed GSTR-3B?" htmlFor="s-3b" hint="Used for Rule 37A. Set it if you know.">
        <Select id="s-3b" disabled={disabled} value={f.gstr3b} onChange={(e) => setF({ ...f, gstr3b: e.target.value })}><option value="">Don't change</option><option value="yes">Yes, filed</option><option value="no">No — not filed</option></Select>
      </Field>
      {!disabled && <Button type="submit" loading={saving}>Save profile</Button>}
    </form>
  );
}
