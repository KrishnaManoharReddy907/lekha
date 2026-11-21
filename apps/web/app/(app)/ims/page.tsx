"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, Wand2 } from "lucide-react";
import { useMemo, useState } from "react";
import { toast } from "sonner";
import { PeriodPicker } from "@/components/period-picker";
import { Badge, Button, Empty, PageHeader, Select, Stat, cx } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { useClients } from "@/lib/clients";
import { date, inr, inrShort } from "@/lib/format";
import { usePeriods } from "@/lib/gst";
import { useSession } from "@/lib/session";

interface ImsItem {
  id: string;
  supplier_gstin: string;
  supplier_name: string | null;
  number: string;
  invoice_date: string | null;
  doc_type: string;
  itc: string;
  taxable_value: string;
  itc_available: boolean;
  decision: { recommendation: string; reason_text: string; reason_codes: string[]; decided_action: string | null; confidence: number } | null;
}
interface ImsData {
  items: ImsItem[];
  deadline: string;
  summary: Record<string, { count: number; itc: string }>;
  recommended: Record<string, number>;
}

const TONE: Record<string, "ok" | "bad" | "warn" | "neutral"> = { accept: "ok", reject: "bad", pending: "warn" };

export default function ImsPage() {
  const { current } = useClients();
  const { can } = useSession();
  const qc = useQueryClient();
  const { periods, period, setPeriod } = usePeriods(current?.id);
  const [sel, setSel] = useState<Set<string>>(new Set());
  const [filter, setFilter] = useState("");
  const q = useQuery({
    queryKey: ["ims", current?.id, period],
    enabled: !!current && !!period,
    queryFn: () => api<ImsData>("/api/ims", { params: { client_id: current?.id, period } }),
  });
  const decide = useMutation({
    mutationFn: ({ ids, action }: { ids: string[]; action: string | null }) => api<{ updated: number }>("/api/ims/decide", { body: { record_ids: ids, action } }),
    onSuccess: (r) => {
      toast.success(`${r.updated} record${r.updated === 1 ? "" : "s"} updated`);
      setSel(new Set());
      qc.invalidateQueries({ queryKey: ["ims"] });
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const items = useMemo(() => (q.data?.items ?? []).filter((i) => !filter || (i.decision?.decided_action ?? `rec:${i.decision?.recommendation}`) === filter || i.decision?.recommendation === filter), [q.data, filter]);
  const daysLeft = q.data ? Math.ceil((new Date(q.data.deadline).getTime() - Date.now()) / 86400000) : null;
  const undecided = (q.data?.items ?? []).filter((i) => !i.decision?.decided_action).map((i) => i.id);

  return (
    <>
      <PageHeader
        title="IMS actions"
        sub="Accept, reject or keep pending each supplier record before GSTR-2B is generated. Lekha recommends; you decide."
        actions={
          <>
            {periods.length > 0 && <PeriodPicker periods={periods.filter((p) => p.has_2b)} value={period} onChange={setPeriod} />}
            {current && period && (
              <a className="inline-flex h-9 items-center gap-1.5 rounded-md border border-rule-2 bg-sheet px-3.5 text-sm font-medium hover:bg-sunk" href={`/api/ims/export?client_id=${current.id}&period=${period}`}>
                <Download className="size-4" /> Export for portal
              </a>
            )}
          </>
        }
      />
      {!q.data ? (
        <Empty title="Import a GSTR-2B first">IMS actions apply to the supplier records in a GSTR-2B.</Empty>
      ) : (
        <>
          <div className="mb-5 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
            <Stat label="Deadline" value={date(q.data.deadline)} sub={daysLeft !== null ? (daysLeft >= 0 ? `${daysLeft} days left` : "passed") : ""} tone={daysLeft !== null && daysLeft <= 3 ? "bad" : undefined} />
            <Stat label="Recommended accept" value={q.data.recommended.accept ?? 0} tone="ok" />
            <Stat label="Recommended reject" value={q.data.recommended.reject ?? 0} tone={(q.data.recommended.reject ?? 0) > 0 ? "bad" : undefined} />
            <Stat label="Undecided" value={undecided.length} sub={`No action = deemed accepted · ${inrShort(q.data.summary.no_action?.itc ?? 0)} ITC`} tone={undecided.length ? "warn" : "ok"} />
          </div>
          {can("write") && (
            <div className="mb-3 flex flex-wrap items-center gap-2">
              <Button variant="primary" icon={<Wand2 className="size-4" />} disabled={!undecided.length} loading={decide.isPending && decide.variables?.action === "use_recommendation"} onClick={() => decide.mutate({ ids: undecided, action: "use_recommendation" })}>
                Apply recommendations to {undecided.length} undecided
              </Button>
              {sel.size > 0 && (
                <>
                  <span className="text-[13px] text-ink-3">{sel.size} selected:</span>
                  {(["accept", "reject", "pending"] as const).map((a) => (
                    <Button key={a} size="sm" onClick={() => decide.mutate({ ids: [...sel], action: a })} className="capitalize">{a}</Button>
                  ))}
                  <Button size="sm" variant="ghost" onClick={() => decide.mutate({ ids: [...sel], action: null })}>Clear</Button>
                </>
              )}
              <Select aria-label="Filter" value={filter} onChange={(e) => setFilter(e.target.value)} className="ml-auto w-48">
                <option value="">All records</option>
                <option value="accept">Accept</option>
                <option value="reject">Reject</option>
                <option value="pending">Pending</option>
              </Select>
            </div>
          )}
          <div className="overflow-x-auto rounded-lg border border-rule bg-sheet">
            <table className="w-full min-w-[880px] text-[13px]">
              <thead>
                <tr className="bg-sunk text-left text-[12px] text-ink-2">
                  <th className="w-10 px-3 py-2">
                    <input type="checkbox" aria-label="Select all" checked={items.length > 0 && items.every((i) => sel.has(i.id))} onChange={(e) => setSel(e.target.checked ? new Set(items.map((i) => i.id)) : new Set())} />
                  </th>
                  <th className="px-3 py-2 font-semibold">Supplier</th>
                  <th className="px-3 py-2 font-semibold">Document</th>
                  <th className="px-3 py-2 text-right font-semibold">ITC</th>
                  <th className="px-3 py-2 font-semibold">Lekha recommends</th>
                  <th className="px-3 py-2 font-semibold">Your action</th>
                </tr>
              </thead>
              <tbody>
                {items.map((i) => {
                  const d = i.decision;
                  return (
                    <tr key={i.id} className={cx("border-t border-rule", sel.has(i.id) && "bg-accent-soft/40")}>
                      <td className="px-3 py-2">
                        <input type="checkbox" aria-label={`Select ${i.number}`} checked={sel.has(i.id)} onChange={(e) => { const n = new Set(sel); if (e.target.checked) n.add(i.id); else n.delete(i.id); setSel(n); }} />
                      </td>
                      <td className="max-w-[220px] px-3 py-2">
                        <div className="truncate font-medium">{i.supplier_name || i.supplier_gstin}</div>
                        <div className="num text-[11.5px] text-ink-3">{i.supplier_gstin}</div>
                      </td>
                      <td className="px-3 py-2">
                        <span className="num">{i.number}</span>
                        {i.doc_type !== "invoice" && <Badge className="ml-1" tone="info">{i.doc_type.replace("_", " ")}</Badge>}
                        <div className="text-[11.5px] text-ink-3">{date(i.invoice_date)}</div>
                      </td>
                      <td className="num px-3 py-2 text-right">{inr(i.itc)}</td>
                      <td className="max-w-[360px] px-3 py-2">
                        {d && <Badge tone={TONE[d.recommendation]} className="capitalize">{d.recommendation}</Badge>}
                        <p className="mt-0.5 text-[12px] text-ink-3">{d?.reason_text}</p>
                      </td>
                      <td className="px-3 py-2">
                        {can("write") ? (
                          <Select aria-label={`Action for ${i.number}`} value={d?.decided_action ?? ""} onChange={(e) => decide.mutate({ ids: [i.id], action: e.target.value || null })} className="h-8 w-36">
                            <option value="">No action</option>
                            <option value="accept">Accept</option>
                            <option value="reject">Reject</option>
                            <option value="pending">Pending</option>
                          </Select>
                        ) : <span className="capitalize">{d?.decided_action ?? "No action"}</span>}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <p className="mt-3 text-[12.5px] text-ink-3">Apply these on the GST portal (Services → Returns → Invoice Management System) or upload the exported file into the IMS offline tool.</p>
        </>
      )}
    </>
  );
}
