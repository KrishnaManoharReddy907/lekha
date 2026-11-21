"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, FilePlus2, RefreshCw, Scale } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { Dialog } from "@/components/dialog";
import { Badge, Button, Card, CardHeader, Empty, Field, Input, PageHeader, Select, Textarea, cx } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { useClients } from "@/lib/clients";
import { date, inr, period as fmtPeriod } from "@/lib/format";
import { usePeriods } from "@/lib/gst";
import { useSession } from "@/lib/session";

interface Pack {
  id: string;
  version: number;
  reply_text: string;
  zip_url: string | null;
  created_at: string;
  analysis: { rows: { period: string; basis: string; claimed: string; available_2b: string; difference: string; unexplained: string; explained: { category: string; reason: string; amount: string }[] }[]; totals: Record<string, string> };
}
interface Notice { id: string; form: string; reference: string | null; period_from: string; period_to: string; amount: string; received_on: string | null; reply_due: string | null; status: string; packs?: Pack[] }

export default function NoticesPage() {
  const { current } = useClients();
  const { can } = useSession();
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [sel, setSel] = useState<string | null>(null);
  const list = useQuery({ queryKey: ["notices", current?.id], enabled: !!current, queryFn: () => api<Notice[]>("/api/notices", { params: { client_id: current?.id } }) });
  useEffect(() => { if (!sel && list.data?.length) setSel(list.data[0].id); }, [list.data, sel]);
  const detail = useQuery({
    queryKey: ["notice", sel],
    enabled: !!sel,
    queryFn: () => api<Notice>(`/api/notices/${sel}`),
    // The pack is built in the background; check back until it appears.
    refetchInterval: (q) => (q.state.data && !q.state.data.packs?.length ? 2000 : false),
  });
  const rebuild = useMutation({
    mutationFn: () => api(`/api/notices/${sel}/rebuild`, { method: "POST" }),
    onSuccess: () => { toast.success("Rebuilding from the latest data…"); setTimeout(() => qc.invalidateQueries({ queryKey: ["notice", sel] }), 3000); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const pack = detail.data?.packs?.[0];
  return (
    <>
      <PageHeader
        title="Notices"
        sub="Got a DRC-01C or ASMT-10? Lekha rebuilds each period from your records, explains the difference with evidence, and drafts the reply."
        actions={can("write") && <Button variant="primary" icon={<FilePlus2 className="size-4" />} onClick={() => setOpen(true)}>Record a notice</Button>}
      />
      {!list.data?.length ? (
        <Empty icon={<Scale className="size-8" />} title="No notices">When a notice arrives, record its form, periods and amount — the defence pack is ready in seconds.</Empty>
      ) : (
        <div className="grid gap-5 lg:grid-cols-[260px_1fr]">
          <div className="flex flex-col gap-2">
            {list.data.map((n) => (
              <button key={n.id} onClick={() => setSel(n.id)} className={cx("rounded-lg border p-3 text-left", sel === n.id ? "border-accent bg-accent-soft/50" : "border-rule bg-sheet hover:bg-sunk")}>
                <div className="flex items-center justify-between"><span className="font-medium">{n.form}</span><span className="num text-[13px]">{inr(n.amount, { paise: false })}</span></div>
                <div className="text-[12px] text-ink-3">{fmtPeriod(n.period_from)} – {fmtPeriod(n.period_to)} · reply by {date(n.reply_due)}</div>
              </button>
            ))}
          </div>
          {detail.data && !pack && (
            <Card className="flex items-center gap-3 text-[14px]">
              <span className="size-4 animate-spin rounded-full border-2 border-accent border-t-transparent" aria-hidden />
              Building the defence pack: rebuilding each period, gathering evidence and drafting the reply…
            </Card>
          )}
          {pack && detail.data && (
            <div className="flex flex-col gap-4">
              <Card>
                <CardHeader
                  title={`${detail.data.form}${detail.data.reference ? ` · ${detail.data.reference}` : ""}`}
                  sub={`Defence pack v${pack.version}`}
                  action={
                    <div className="flex gap-2">
                      {can("write") && <Button size="sm" icon={<RefreshCw className="size-3.5" />} loading={rebuild.isPending} onClick={() => rebuild.mutate()}>Rebuild</Button>}
                      {pack.zip_url && <a href={pack.zip_url} className="inline-flex h-8 items-center gap-1.5 rounded-md bg-accent px-2.5 text-[13px] font-medium text-accent-ink"><Download className="size-3.5" /> Download pack (ZIP)</a>}
                    </div>
                  }
                />
                <div className="grid gap-3 sm:grid-cols-4">
                  {[["Claimed in 3B", pack.analysis.totals.claimed], ["Available in 2B", pack.analysis.totals.available_2b], ["Explained", pack.analysis.totals.explained], ["Unexplained", pack.analysis.totals.unexplained]].map(([l, v]) => (
                    <div key={l} className="rounded-md bg-sunk p-3"><div className="text-[11.5px] text-ink-3 uppercase">{l}</div><div className={cx("num mt-0.5 font-semibold", l === "Unexplained" && Number(v) > 0 && "text-red")}>{inr(v, { paise: false })}</div></div>
                  ))}
                </div>
                <div className="mt-4 overflow-x-auto">
                  <table className="w-full min-w-[620px] text-[12.5px]">
                    <thead><tr className="text-left text-ink-3"><th className="py-1.5">Period</th><th className="text-right">Claimed</th><th className="text-right">In 2B</th><th className="text-right">Difference</th><th className="pl-3">Explained by</th></tr></thead>
                    <tbody>
                      {pack.analysis.rows.map((r) => (
                        <tr key={r.period} className="border-t border-rule align-top">
                          <td className="py-1.5">{fmtPeriod(r.period)}<div className="text-[11px] text-ink-3">{r.basis}</div></td>
                          <td className="num text-right">{inr(r.claimed, { paise: false })}</td>
                          <td className="num text-right">{inr(r.available_2b, { paise: false })}</td>
                          <td className="num text-right">{inr(r.difference, { paise: false })}</td>
                          <td className="pl-3">{r.explained.length ? r.explained.map((e) => <div key={e.category}><Badge>{e.category}</Badge> {inr(e.amount, { paise: false })}</div>) : <span className="text-ink-3">—</span>}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </Card>
              <ReplyEditor pack={pack} canEdit={can("write")} />
            </div>
          )}
        </div>
      )}
      <NewNotice open={open} onOpenChange={setOpen} onCreated={(id) => { setSel(id); qc.invalidateQueries({ queryKey: ["notices"] }); }} />
    </>
  );
}

function ReplyEditor({ pack, canEdit }: { pack: Pack; canEdit: boolean }) {
  const [text, setText] = useState(pack.reply_text);
  useEffect(() => setText(pack.reply_text), [pack.id, pack.reply_text]);
  const save = useMutation({
    mutationFn: () => api(`/api/notice-packs/${pack.id}`, { method: "PATCH", body: { reply_text: text } }),
    onSuccess: () => toast.success("Reply saved"),
  });
  return (
    <Card>
      <CardHeader title="Reply draft" sub="Edit freely. Every amount comes from your records; Lekha never invents facts." action={canEdit && text !== pack.reply_text && <Button size="sm" onClick={() => save.mutate()} loading={save.isPending}>Save</Button>} />
      <Textarea aria-label="Reply draft" rows={18} className="font-mono text-[12.5px]" value={text} disabled={!canEdit} onChange={(e) => setText(e.target.value)} />
    </Card>
  );
}

function NewNotice({ open, onOpenChange, onCreated }: { open: boolean; onOpenChange: (o: boolean) => void; onCreated: (id: string) => void }) {
  const { current } = useClients();
  const { periods } = usePeriods(current?.id);
  const p2b = periods.filter((p) => p.has_2b).map((p) => p.period);
  const [f, setF] = useState({ form: "DRC-01C", reference: "", period_from: "", period_to: "", amount: "" });
  useEffect(() => { if (p2b.length && !f.period_to) setF((x) => ({ ...x, period_from: p2b[Math.min(2, p2b.length - 1)], period_to: p2b[0] })); }, [p2b, f.period_to]);
  const m = useMutation({
    mutationFn: () => api<{ id: string }>("/api/notices", { body: { client_id: current?.id, ...f, reference: f.reference || null, amount: f.amount || "0" } }),
    onSuccess: (n) => { onCreated(n.id); onOpenChange(false); toast.success("Notice recorded — building the defence pack"); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  return (
    <Dialog open={open} onOpenChange={onOpenChange} title="Record a notice" description="Lekha builds the defence pack immediately.">
      <form className="flex flex-col gap-4" onSubmit={(e) => { e.preventDefault(); m.mutate(); }}>
        <Field label="Form" htmlFor="n-form">
          <Select id="n-form" value={f.form} onChange={(e) => setF({ ...f, form: e.target.value })}>
            <option value="DRC-01C">DRC-01C — ITC in 3B exceeds GSTR-2B</option>
            <option value="ASMT-10">ASMT-10 — scrutiny discrepancies</option>
            <option value="DRC-01B">DRC-01B — GSTR-1 vs 3B liability</option>
            <option value="OTHER">Other</option>
          </Select>
        </Field>
        <Field label="Reference number (optional)" htmlFor="n-ref"><Input id="n-ref" value={f.reference} onChange={(e) => setF({ ...f, reference: e.target.value })} /></Field>
        <div className="grid grid-cols-2 gap-3">
          <Field label="From period" htmlFor="n-from"><Select id="n-from" value={f.period_from} onChange={(e) => setF({ ...f, period_from: e.target.value })}>{p2b.map((p) => <option key={p} value={p}>{fmtPeriod(p)}</option>)}</Select></Field>
          <Field label="To period" htmlFor="n-to"><Select id="n-to" value={f.period_to} onChange={(e) => setF({ ...f, period_to: e.target.value })}>{p2b.map((p) => <option key={p} value={p}>{fmtPeriod(p)}</option>)}</Select></Field>
        </div>
        <Field label="Difference amount in the notice (₹)" htmlFor="n-amt"><Input id="n-amt" inputMode="decimal" className="num" required value={f.amount} onChange={(e) => setF({ ...f, amount: e.target.value })} /></Field>
        <Button type="submit" variant="primary" loading={m.isPending}>Build defence pack</Button>
      </form>
    </Dialog>
  );
}
