"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, Lock } from "lucide-react";
import { Fragment, useEffect, useState } from "react";
import { toast } from "sonner";
import { PeriodPicker } from "@/components/period-picker";
import { Badge, Button, Card, Empty, PageHeader, Select, Spinner, cx } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { useClients } from "@/lib/clients";
import { date, inr, period as fmtPeriod } from "@/lib/format";
import { usePeriods } from "@/lib/gst";
import { useSession } from "@/lib/session";

type Cell = { igst: string; cgst: string; sgst: string; cess: string; total: string; label: string };
interface T4 {
  cells: Record<string, Cell>;
  sources: Record<string, { ref: string; label: string; amount: string; reason?: string }[]>;
  period: string;
  gstin: string;
  records: number;
  included: number;
  excluded_by_ims: number;
  finalised: { created_at: string } | null;
}
const ORDER = ["4A1", "4A3", "4A4", "4A5", "4B1", "4B2", "4C", "4D1", "4D2"];

export default function Table4Page() {
  const { current } = useClients();
  const { can } = useSession();
  const qc = useQueryClient();
  const { periods, period, setPeriod } = usePeriods(current?.id);
  const [gstin, setGstin] = useState<string>("");
  const [open, setOpen] = useState<string | null>(null);
  useEffect(() => setGstin(current?.gstins[0]?.gstin ?? ""), [current]);
  const q = useQuery({
    queryKey: ["table4", current?.id, gstin, period],
    enabled: !!current && !!gstin && !!period,
    queryFn: () => api<T4>("/api/table4", { params: { client_id: current?.id, gstin, period } }),
    retry: false,
  });
  const fin = useMutation({
    mutationFn: () => api(`/api/clients/${current!.id}/table4/finalise`, { body: { gstin, period } }),
    onSuccess: () => { toast.success("Table 4 finalised — claimed invoices recorded"); qc.invalidateQueries({ queryKey: ["table4"] }); qc.invalidateQueries({ queryKey: ["risk"] }); },
    onError: (e) => toast.error(errorMessage(e)),
  });

  return (
    <>
      <PageHeader
        title="GSTR-3B Table 4"
        sub="Eligible ITC computed from GSTR-2B, your IMS actions, your books and the risk ledger. Click any row to see what makes it up."
        actions={
          <>
            {current && current.gstins.length > 1 && (
              <Select aria-label="GSTIN" value={gstin} onChange={(e) => setGstin(e.target.value)} className="w-52">
                {current.gstins.map((g) => <option key={g.id} value={g.gstin}>{g.gstin}</option>)}
              </Select>
            )}
            {periods.length > 0 && <PeriodPicker periods={periods.filter((p) => p.has_2b)} value={period} onChange={setPeriod} />}
          </>
        }
      />
      {q.isLoading ? (
        <div className="grid h-40 place-items-center"><Spinner className="size-6" /></div>
      ) : q.isError ? (
        <Empty title="Nothing to compute yet">{errorMessage(q.error)}</Empty>
      ) : q.data ? (
        <div className="grid gap-5 xl:grid-cols-[1fr_320px]">
          <div className="overflow-x-auto rounded-lg border border-rule bg-sheet">
            <table className="w-full min-w-[720px] text-[13.5px]">
              <thead>
                <tr className="bg-sunk text-left text-[12px] text-ink-2">
                  <th className="px-3 py-2 font-semibold">Details</th>
                  <th className="px-3 py-2 text-right font-semibold">Integrated tax</th>
                  <th className="px-3 py-2 text-right font-semibold">Central tax</th>
                  <th className="px-3 py-2 text-right font-semibold">State/UT tax</th>
                  <th className="px-3 py-2 text-right font-semibold">Cess</th>
                </tr>
              </thead>
              <tbody>
                {ORDER.map((k) => {
                  const c = q.data!.cells[k];
                  const src = q.data!.sources[k] ?? [];
                  const net = k === "4C";
                  return (
                    <Fragment key={k}>
                      <tr
                        onClick={() => src.length && setOpen(open === k ? null : k)}
                        className={cx("border-t border-rule", src.length && "cursor-pointer hover:bg-sunk/60", net && "bg-accent-soft/50 font-semibold", (k === "4D1" || k === "4A1") && "border-t-2 border-t-rule-2")}
                      >
                        <td className="px-3 py-2.5">
                          {c.label}
                          {src.length > 0 && <span className="ml-2 text-[11.5px] font-normal text-ink-3">{src.length} item{src.length > 1 ? "s" : ""}</span>}
                        </td>
                        {(["igst", "cgst", "sgst", "cess"] as const).map((h) => (
                          <td key={h} className="num px-3 py-2.5 text-right">{Number(c[h]) ? inr(c[h]) : "0.00"}</td>
                        ))}
                      </tr>
                      {open === k && (
                        <tr className="bg-paper">
                          <td colSpan={5} className="px-3 py-3">
                            <ul className="max-h-72 divide-y divide-rule overflow-y-auto rounded-md border border-rule bg-sheet">
                              {src.map((s, i) => (
                                <li key={i} className="flex items-center justify-between gap-3 px-3 py-1.5 text-[12.5px]">
                                  <span className="min-w-0 truncate">{s.label}{s.reason && <span className="ml-2 text-ink-3">— {s.reason}</span>}</span>
                                  <span className="num shrink-0">{inr(s.amount)}</span>
                                </li>
                              ))}
                            </ul>
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
          <div className="flex flex-col gap-3">
            <Card>
              <div className="text-[12px] font-semibold tracking-wider text-ink-3 uppercase">Net ITC to claim · {fmtPeriod(q.data.period)}</div>
              <div className="num mt-1 text-[28px] font-semibold text-accent">{inr(q.data.cells["4C"].total)}</div>
              <p className="mt-2 text-[12.5px] text-ink-3">{q.data.included} of {q.data.records} GSTR-2B records included · {q.data.excluded_by_ims} held back by IMS (rejected/pending).</p>
              {q.data.finalised ? (
                <p className="mt-3 flex items-center gap-1.5 text-[13px] text-ok"><Lock className="size-4" /> Finalised {date(q.data.finalised.created_at)}</p>
              ) : can("write") && (
                <Button variant="primary" className="mt-4 w-full" icon={<CheckCircle2 className="size-4" />} loading={fin.isPending} onClick={() => fin.mutate()}>
                  Finalise for filing
                </Button>
              )}
            </Card>
            <Card className="text-[12.5px] text-ink-2">
              <p className="mb-1 font-medium text-ink">How these numbers are built</p>
              <p>4A(5) is the ITC in GSTR-2B you accepted (no action counts as accepted). 4B(1) reverses blocked credit under Sec 17(5). 4B(2) temporarily reverses credit that is unpaid after 180 days, not yet received, over-reported by the supplier, or not in your books. 4D(1) re-claims earlier reversals that are now cleared.</p>
              <Badge tone="warn" className="mt-2">Review before filing — Lekha recommends, you decide.</Badge>
            </Card>
          </div>
        </div>
      ) : null}
    </>
  );
}
