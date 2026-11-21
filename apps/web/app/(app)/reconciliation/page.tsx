"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, ChevronDown, ChevronRight, CircleAlert, CircleX, RefreshCw, TrendingUp, XCircle } from "lucide-react";
import Link from "next/link";
import { Fragment, useState } from "react";
import { toast } from "sonner";
import { PeriodPicker } from "@/components/period-picker";
import { Badge, Button, Card, Empty, PageHeader, Spinner, Stat, StatusBadge, cx } from "@/components/ui";
import { Upload2B } from "@/components/upload-2b";
import { api, errorMessage } from "@/lib/api";
import { useClients } from "@/lib/clients";
import { date, inr, inrShort, pct, period as fmtPeriod } from "@/lib/format";
import { MISMATCH_LABEL, type MatchRow, usePeriods } from "@/lib/gst";
import { useSession } from "@/lib/session";

interface Recon {
  period: string;
  counts: Record<string, number>;
  itc: { books: string; gstr2b: string; matched: string };
  items: MatchRow[];
  import: { generated_on: string | null; filename: string; record_count: number } | null;
  ims_deadline: string;
}

const TABS: { key: string; label: string }[] = [
  { key: "", label: "All" },
  { key: "missing_in_2b", label: "Missing in 2B" },
  { key: "mismatch", label: "Mismatch" },
  { key: "missing_in_books", label: "Missing in books" },
  { key: "matched", label: "Matched" },
];

export default function ReconciliationPage() {
  const { current } = useClients();
  const { can } = useSession();
  const qc = useQueryClient();
  const { periods, period, setPeriod } = usePeriods(current?.id);
  const [tab, setTab] = useState("");
  const [open, setOpen] = useState<string | null>(null);
  const q = useQuery({
    queryKey: ["recon", current?.id, period],
    enabled: !!current && !!period,
    queryFn: () => api<Recon>("/api/reconciliation", { params: { client_id: current?.id, period } }),
    refetchInterval: (qq) => (qq.state.data && qq.state.data.items.length === 0 && qq.state.data.import ? 2000 : false),
  });
  const rerun = useMutation({
    mutationFn: () => api(`/api/clients/${current!.id}/reconcile`, { body: { period } }),
    onSuccess: () => {
      toast.success("Reconciled again");
      qc.invalidateQueries({ queryKey: ["recon"] });
    },
    onError: (e) => toast.error(errorMessage(e)),
  });

  if (!current) return <PageHeader title="Reconciliation" />;
  const data = q.data;
  const hasTwoB = periods.find((p) => p.period === period)?.has_2b;
  const items = (data?.items ?? []).filter((i) => !tab || i.status === tab);
  const c = data?.counts ?? {};
  const missingItc = (data?.items ?? []).filter((i) => i.status === "missing_in_2b").reduce((s, i) => s + Number(i.itc_books), 0);
  const mismatchGap = (data?.items ?? []).filter((i) => i.status === "mismatch").reduce((s, i) => s + Math.max(0, Number(i.itc_books) - Number(i.itc_2b)), 0);

  return (
    <>
      <PageHeader
        title="Reconciliation"
        sub={<>Your purchase register against GSTR-2B for <span className="font-medium text-ink">{current.name}</span>. Every result explains itself.</>}
        actions={
          <>
            {periods.length > 0 && <PeriodPicker periods={periods} value={period} onChange={setPeriod} />}
            {can("write") && hasTwoB && <Button icon={<RefreshCw className="size-4" />} onClick={() => rerun.mutate()} loading={rerun.isPending}>Re-run</Button>}
            {can("write") && <Upload2B client={current} onDone={(p) => setPeriod(p)} />}
          </>
        }
      />
      {!period || (!hasTwoB && !q.isLoading) ? (
        <Empty
          icon={<TrendingUp className="size-8" />}
          title={period ? `No GSTR-2B for ${fmtPeriod(period)} yet` : "Import a GSTR-2B to start"}
        >
          On the GST portal go to <b>Returns → GSTR-2B</b>, pick the month and click <b>Download JSON</b> (or Excel). Import it here and Lekha matches every record with your books in seconds.
        </Empty>
      ) : !data ? (
        <div className="grid h-40 place-items-center"><Spinner className="size-6" /></div>
      ) : (
        <>
          <div className="mb-5 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
            <Stat label="Matched ITC" value={inrShort(data.itc.matched)} sub={`${c.matched ?? 0} invoices`} tone="ok" />
            <Stat label="Missing in 2B" value={inrShort(missingItc)} sub={`${c.missing_in_2b ?? 0} invoices — supplier hasn't reported`} tone={c.missing_in_2b ? "bad" : undefined} />
            <Stat label="Mismatch gap" value={inrShort(mismatchGap)} sub={`${c.mismatch ?? 0} invoices with differences`} tone={c.mismatch ? "warn" : undefined} />
            <Stat label="Missing in books" value={c.missing_in_books ?? 0} sub="In 2B but not received/entered" />
          </div>
          <div className="mb-3 flex flex-wrap items-center gap-1 border-b border-rule">
            {TABS.map((t) => (
              <button
                key={t.key}
                onClick={() => setTab(t.key)}
                className={cx("-mb-px border-b-2 px-3 py-2 text-[13.5px] transition", tab === t.key ? "border-accent font-medium text-accent" : "border-transparent text-ink-2 hover:text-ink")}
              >
                {t.label}
                <span className="num ml-1.5 text-[12px] text-ink-3">{t.key ? c[t.key] ?? 0 : data.items.length}</span>
              </button>
            ))}
            <span className="ml-auto pb-2 text-[12.5px] text-ink-3">
              IMS actions due by <b className="text-ink">{date(data.ims_deadline)}</b> ·{" "}
              <Link className="text-accent" href="/ims">Open IMS</Link>
            </span>
          </div>
          <div className="overflow-x-auto rounded-lg border border-rule bg-sheet">
            <table className="w-full min-w-[920px] border-collapse text-[13px]">
              <thead>
                <tr className="bg-sunk text-left text-[12px] text-ink-2">
                  <th className="w-8" />
                  <th className="px-3 py-2 font-semibold">Supplier</th>
                  <th className="px-3 py-2 font-semibold">In your books</th>
                  <th className="px-3 py-2 font-semibold">In GSTR-2B</th>
                  <th className="px-3 py-2 text-right font-semibold">ITC books</th>
                  <th className="px-3 py-2 text-right font-semibold">ITC 2B</th>
                  <th className="px-3 py-2 font-semibold">Result</th>
                </tr>
              </thead>
              <tbody>
                {items.map((m) => {
                  const isOpen = open === m.id;
                  const sup = m.invoice?.supplier_name || m.record?.supplier_name || m.invoice?.supplier_gstin || m.record?.supplier_gstin;
                  return (
                    <Fragment key={m.id}>
                      <tr className="cursor-pointer border-t border-rule hover:bg-sunk/60" onClick={() => setOpen(isOpen ? null : m.id)}>
                        <td className="pl-3 text-ink-3">{isOpen ? <ChevronDown className="size-4" /> : <ChevronRight className="size-4" />}</td>
                        <td className="max-w-[220px] px-3 py-2">
                          <div className="truncate font-medium">{sup}</div>
                          <div className="num text-[11.5px] text-ink-3">{m.invoice?.supplier_gstin || m.record?.supplier_gstin}</div>
                        </td>
                        <td className="px-3 py-2">
                          {m.invoice ? (
                            <>
                              <Link href={`/review/${m.invoice.id}`} onClick={(e) => e.stopPropagation()} className="num text-accent hover:underline">{m.invoice.number}</Link>
                              <div className="text-[11.5px] text-ink-3">{date(m.invoice.date)}</div>
                            </>
                          ) : <span className="text-ink-3">—</span>}
                        </td>
                        <td className="px-3 py-2">
                          {m.record ? (
                            <>
                              <span className="num">{m.record.number}</span>
                              {m.record.doc_type !== "invoice" && <Badge className="ml-1" tone="info">{m.record.doc_type.replace("_", " ")}</Badge>}
                              <div className="text-[11.5px] text-ink-3">{date(m.record.date)}</div>
                            </>
                          ) : <span className="text-ink-3">—</span>}
                        </td>
                        <td className="num px-3 py-2 text-right">{m.invoice ? inr(m.itc_books) : "—"}</td>
                        <td className="num px-3 py-2 text-right">{m.record ? inr(m.itc_2b) : "—"}</td>
                        <td className="px-3 py-2">
                          <div className="flex flex-wrap items-center gap-1">
                            <StatusBadge status={m.status} />
                            {m.mismatch_types.filter((t) => !["NOT_IN_2B", "NOT_IN_BOOKS"].includes(t)).slice(0, 2).map((t) => (
                              <Badge key={t} tone="neutral">{MISMATCH_LABEL[t] ?? t}</Badge>
                            ))}
                            {m.forecast && <Badge tone="info" title={m.forecast.explanation}>{pct(m.forecast.p_next)} next month</Badge>}
                          </div>
                        </td>
                      </tr>
                      {isOpen && (
                        <tr className="bg-paper">
                          <td />
                          <td colSpan={6} className="px-3 pt-2 pb-4">
                            <ExplainPanel m={m} />
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  );
                })}
                {items.length === 0 && (
                  <tr><td colSpan={7} className="px-3 py-10 text-center text-ink-3">Nothing in this view.</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </>
      )}
    </>
  );
}

function ExplainPanel({ m }: { m: MatchRow }) {
  return (
    <div className="grid gap-4 lg:grid-cols-[1.4fr_1fr]">
      <Card className="p-4">
        <div className="mb-2 text-[12px] font-semibold tracking-wider text-ink-3 uppercase">Why Lekha says “{m.explanation.summary}”</div>
        <ul className="flex flex-col gap-1.5">
          {m.explanation.lines.map((l, i) => (
            <li key={i} className="flex items-start gap-2 text-[13px]">
              {l.ok ? <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-ok" /> : l.feature === "presence" ? <CircleX className="mt-0.5 size-4 shrink-0 text-red" /> : <CircleAlert className="mt-0.5 size-4 shrink-0 text-amber" />}
              <span>{l.text}</span>
            </li>
          ))}
        </ul>
        {m.score > 0 && <p className="mt-3 text-[12px] text-ink-3">Match score {pct(m.score)} · method: {m.kind}</p>}
      </Card>
      <div className="flex flex-col gap-3">
        {m.ims && (
          <Card className="p-4">
            <div className="mb-1 text-[12px] font-semibold tracking-wider text-ink-3 uppercase">IMS recommendation</div>
            <p className="text-[13px]"><Badge tone={m.ims.recommendation === "accept" ? "ok" : m.ims.recommendation === "reject" ? "bad" : "warn"}>{m.ims.recommendation}</Badge> {m.ims.reason_text}</p>
          </Card>
        )}
        {m.forecast && (
          <Card className="p-4">
            <div className="mb-1 text-[12px] font-semibold tracking-wider text-ink-3 uppercase">Will this credit come back?</div>
            <p className="text-[13px]">{m.forecast.explanation}</p>
          </Card>
        )}
        {m.record && !m.record.itc_available && (
          <Card className="p-4 text-[13px]"><XCircle className="mb-1 size-4 text-red" />GSTR-2B marks this ITC as not available{m.record.reason ? `: ${m.record.reason}` : ""}.</Card>
        )}
      </div>
    </div>
  );
}
