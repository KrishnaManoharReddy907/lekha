"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CalendarClock, RefreshCw, ShieldCheck } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { toast } from "sonner";
import { Badge, Button, Empty, PageHeader, Select, Stat, cx } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { useClients } from "@/lib/clients";
import { date, inr, inrShort } from "@/lib/format";
import { RISK_LABEL } from "@/lib/gst";
import { useSession } from "@/lib/session";

interface RiskItem {
  id: string;
  kind: string;
  severity: "low" | "medium" | "high" | "critical";
  amount: string;
  interest: string;
  due_date: string | null;
  title: string;
  detail: string;
  rule: string | null;
  status: string;
  invoice_id: string | null;
  supplier_gstin: string | null;
}
interface Ledger {
  items: RiskItem[];
  totals: { itc_at_risk: string; deduction_at_risk: string; by_kind: Record<string, string> };
}

const SEV: Record<string, "bad" | "warn" | "neutral" | "info"> = { critical: "bad", high: "bad", medium: "warn", low: "neutral" };

export default function RiskPage() {
  const { current } = useClients();
  const { can } = useSession();
  const qc = useQueryClient();
  const [kind, setKind] = useState("");
  const q = useQuery({
    queryKey: ["risk", current?.id],
    enabled: !!current,
    queryFn: () => api<Ledger>("/api/risk", { params: { client_id: current?.id } }),
  });
  const refresh = useMutation({
    mutationFn: () => api(`/api/clients/${current!.id}/refresh`, { method: "POST" }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["risk"] }); toast.success("Re-checked against today's rules"); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const setStatus = useMutation({
    mutationFn: ({ id, status }: { id: string; status: string }) => api(`/api/risk/${id}/status`, { body: { status } }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["risk"] }),
  });
  const items = (q.data?.items ?? []).filter((r) => !kind || r.kind === kind);
  const byKind = q.data?.totals.by_kind ?? {};
  const soon = (q.data?.items ?? []).filter((r) => r.due_date && new Date(r.due_date).getTime() - Date.now() < 15 * 86400000);

  return (
    <>
      <PageHeader
        title="Credit at risk"
        sub="Every rupee of input tax credit that could be lost — and what to do before its deadline."
        actions={can("write") && current && <Button icon={<RefreshCw className="size-4" />} onClick={() => refresh.mutate()} loading={refresh.isPending}>Re-check now</Button>}
      />
      {!q.data ? null : q.data.items.length === 0 ? (
        <Empty icon={<ShieldCheck className="size-8" />} title="No credit at risk">Approved invoices are matched, paid in time and inside their deadlines.</Empty>
      ) : (
        <>
          <div className="mb-5 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
            <Stat label="ITC at risk" value={inrShort(q.data.totals.itc_at_risk)} sub="Working capital trapped" tone="bad" />
            <Stat label="Due in 15 days" value={soon.length} sub={inrShort(soon.reduce((s, r) => s + Number(r.amount), 0))} tone={soon.length ? "warn" : undefined} />
            <Stat label="Income-tax deduction at risk" value={inrShort(q.data.totals.deduction_at_risk)} sub="Unpaid MSME bills · Sec 43B(h)" tone={Number(q.data.totals.deduction_at_risk) > 0 ? "warn" : undefined} />
            <Stat label="Open items" value={q.data.items.length} />
          </div>
          <div className="mb-4 flex flex-wrap gap-2">
            <button onClick={() => setKind("")} className={cx("rounded-full border px-3 py-1 text-[12.5px]", !kind ? "border-accent bg-accent-soft text-accent" : "border-rule-2 text-ink-2 hover:bg-sunk")}>All</button>
            {Object.entries(byKind).sort((a, b) => Number(b[1]) - Number(a[1])).map(([k, v]) => (
              <button key={k} onClick={() => setKind(k)} className={cx("rounded-full border px-3 py-1 text-[12.5px]", kind === k ? "border-accent bg-accent-soft text-accent" : "border-rule-2 text-ink-2 hover:bg-sunk")}>
                {RISK_LABEL[k] ?? k} <span className="num ml-1 text-ink-3">{inrShort(v)}</span>
              </button>
            ))}
          </div>
          <ul className="flex flex-col gap-2">
            {items.map((r) => {
              const days = r.due_date ? Math.ceil((new Date(r.due_date).getTime() - Date.now()) / 86400000) : null;
              return (
                <li key={r.id} className={cx("grid gap-3 rounded-lg border bg-sheet p-4 sm:grid-cols-[1fr_auto]", r.severity === "critical" ? "border-red/40" : "border-rule")}>
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <Badge tone={SEV[r.severity]} className="capitalize">{r.severity}</Badge>
                      <Badge tone="neutral">{RISK_LABEL[r.kind] ?? r.kind}</Badge>
                      {r.status === "accepted" && <Badge tone="info">Acknowledged</Badge>}
                      {r.status === "reversed" && <Badge tone="info">Reversed in 3B</Badge>}
                      {r.status === "reclaimable" && <Badge tone="ok">Can re-claim in 4D(1)</Badge>}
                    </div>
                    <p className="mt-1.5 font-medium">{r.invoice_id ? <Link href={`/review/${r.invoice_id}`} className="hover:text-accent">{r.title}</Link> : r.title}</p>
                    <p className="mt-0.5 text-[13px] text-ink-2">{r.detail}</p>
                    {r.rule && <p className="mt-1 text-[11.5px] text-ink-3">Rule {r.rule}</p>}
                  </div>
                  <div className="flex flex-row items-end justify-between gap-3 sm:flex-col sm:items-end">
                    <div className="text-right">
                      <div className="num text-[18px] font-semibold">{inr(r.amount, { paise: false })}</div>
                      {Number(r.interest) > 0 && <div className="num text-[12px] text-red">+ {inr(r.interest)} interest</div>}
                      {r.due_date && (
                        <div className={cx("mt-0.5 flex items-center justify-end gap-1 text-[12.5px]", days !== null && days < 0 ? "text-red" : days !== null && days <= 15 ? "text-amber" : "text-ink-3")}>
                          <CalendarClock className="size-3.5" />
                          {date(r.due_date)} · {days !== null && (days < 0 ? `${-days} d overdue` : `${days} d left`)}
                        </div>
                      )}
                    </div>
                    {can("write") && (
                      <Select aria-label="Status" value={r.status === "open" ? "" : r.status} onChange={(e) => setStatus.mutate({ id: r.id, status: e.target.value || "open" })} className="h-8 w-40">
                        <option value="">Open</option>
                        <option value="accepted">Acknowledged</option>
                        <option value="resolved">Mark resolved</option>
                      </Select>
                    )}
                  </div>
                </li>
              );
            })}
          </ul>
        </>
      )}
    </>
  );
}
