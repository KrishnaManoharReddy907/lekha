"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, Circle, CircleAlert, CircleDashed, Loader2, Play, ShieldCheck, XCircle } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { PeriodPicker } from "@/components/period-picker";
import { Badge, Button, Card, Empty, PageHeader, cx } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { useClients } from "@/lib/clients";
import { date, inr, period as fmtPeriod, relTime } from "@/lib/format";
import { usePeriods } from "@/lib/gst";
import { useSession } from "@/lib/session";

interface Step { id: string; code: string; title: string; status: string; output: Record<string, any>; approval_required: boolean; error: string | null; approved_at: string | null }
interface Run { id: string; period: string; client_gstin: string; status: string; trigger: string; created_at: string; summary: { headline?: string }; steps?: Step[] }

const ICON: Record<string, React.ReactNode> = {
  done: <CheckCircle2 className="size-5 text-ok" />,
  approved: <ShieldCheck className="size-5 text-ok" />,
  needs_approval: <CircleAlert className="size-5 text-amber" />,
  running: <Loader2 className="size-5 animate-spin text-blue" />,
  failed: <XCircle className="size-5 text-red" />,
  skipped: <CircleDashed className="size-5 text-ink-3" />,
  pending: <Circle className="size-5 text-ink-3" />,
};

export default function ClosePage() {
  const { current } = useClients();
  const { can } = useSession();
  const qc = useQueryClient();
  const { periods, period, setPeriod } = usePeriods(current?.id);
  const [sel, setSel] = useState<string | null>(null);
  const runs = useQuery({ queryKey: ["close-runs", current?.id], enabled: !!current, queryFn: () => api<Run[]>("/api/close-runs", { params: { client_id: current?.id } }) });
  useEffect(() => { if (!sel && runs.data?.length) setSel(runs.data[0].id); }, [runs.data, sel]);
  useEffect(() => setSel(null), [current?.id]);
  const run = useQuery({
    queryKey: ["close-run", sel],
    enabled: !!sel,
    queryFn: () => api<Run>(`/api/close-runs/${sel}`),
    refetchInterval: (q) => (q.state.data?.status === "running" ? 1500 : false),
  });
  const start = useMutation({
    mutationFn: () => api<Run>(`/api/clients/${current!.id}/close`, { body: { period } }),
    onSuccess: (r) => { setSel(r.id); qc.invalidateQueries({ queryKey: ["close-runs"] }); toast.success("Close started"); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const approve = useMutation({
    mutationFn: (code: string) => api<Run>(`/api/close-runs/${sel}/steps/${code}/approve`, { method: "POST" }),
    onSuccess: (r) => { qc.setQueryData(["close-run", sel], r); qc.invalidateQueries({ queryKey: ["close-runs"] }); toast.success("Approved"); },
    onError: (e) => toast.error(errorMessage(e)),
  });

  return (
    <>
      <PageHeader
        title="Monthly close"
        sub="When a GSTR-2B arrives, Lekha runs the month for you and stops wherever a person must decide. Nothing is filed or sent without approval."
        actions={can("write") && current && (
          <>
            {periods.length > 0 && <PeriodPicker periods={periods.filter((p) => p.has_2b)} value={period} onChange={setPeriod} />}
            <Button variant="primary" icon={<Play className="size-4" />} loading={start.isPending} disabled={!period} onClick={() => start.mutate()}>Run close</Button>
          </>
        )}
      />
      {!runs.data?.length ? (
        <Empty title="No close runs yet">Import a GSTR-2B (Reconciliation page) and a close starts automatically, or start one here.</Empty>
      ) : (
        <div className="grid gap-5 lg:grid-cols-[260px_1fr]">
          <div className="flex flex-col gap-2">
            {runs.data.map((r) => (
              <button key={r.id} onClick={() => setSel(r.id)} className={cx("rounded-lg border p-3 text-left transition", sel === r.id ? "border-accent bg-accent-soft/50" : "border-rule bg-sheet hover:bg-sunk")}>
                <div className="flex items-center justify-between"><span className="font-medium">{fmtPeriod(r.period)}</span><RunBadge s={r.status} /></div>
                <div className="mt-0.5 text-[12px] text-ink-3">{r.trigger === "gstr2b" ? "Started by GSTR-2B import" : "Started manually"} · {relTime(r.created_at)}</div>
              </button>
            ))}
          </div>
          {run.data && (
            <div>
              {run.data.summary?.headline && (
                <Card className="mb-4 border-accent/30 bg-accent-soft/40"><p className="text-[14px] font-medium">{run.data.summary.headline}</p></Card>
              )}
              <ol className="flex flex-col gap-3">
                {run.data.steps?.map((s, i) => (
                  <li key={s.id} className={cx("rounded-lg border bg-sheet p-4", s.status === "needs_approval" ? "border-amber/40" : "border-rule")}>
                    <div className="flex items-start gap-3">
                      <span className="mt-0.5">{ICON[s.status] ?? ICON.pending}</span>
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="num text-[12px] text-ink-3">{i + 1}</span>
                          <span className="font-medium">{s.title}</span>
                          {s.approval_required && <Badge tone={s.status === "approved" ? "ok" : "neutral"}>{s.status === "approved" ? "Approved" : "Needs a person"}</Badge>}
                        </div>
                        {s.error && <p className="mt-1 text-[13px] text-red">{s.error}</p>}
                        <StepOutput s={s} />
                        {s.status === "needs_approval" && can("write") && (
                          <div className="mt-3 flex flex-wrap items-center gap-3 border-t border-rule pt-3">
                            <Button size="sm" variant="primary" loading={approve.isPending && approve.variables === s.code} onClick={() => approve.mutate(s.code)}>Approve</Button>
                            <span className="text-[12.5px] text-ink-3">{s.output?.approve_means}</span>
                          </div>
                        )}
                      </div>
                    </div>
                  </li>
                ))}
              </ol>
            </div>
          )}
        </div>
      )}
    </>
  );
}

function RunBadge({ s }: { s: string }) {
  const m: Record<string, ["ok" | "warn" | "info" | "bad", string]> = { approved: ["ok", "Closed"], awaiting_approval: ["warn", "Needs approval"], running: ["info", "Running"], failed: ["bad", "Failed"] };
  const [tone, label] = m[s] ?? ["info", s];
  return <Badge tone={tone}>{label}</Badge>;
}

function StepOutput({ s }: { s: Step }) {
  const o = s.output ?? {};
  if (!Object.keys(o).length) return null;
  const line = (t: React.ReactNode) => <p className="mt-1 text-[13px] text-ink-2">{t}</p>;
  switch (s.code) {
    case "intake":
      return line(o.ok ? `All ${o.approved} invoices for the month are reviewed.` : (o.notes ?? []).join(" "));
    case "reconcile":
      return line(<>Matched {o.counts?.matched ?? 0} · mismatch {o.counts?.mismatch ?? 0} · missing in 2B {o.counts?.missing_in_2b ?? 0} · missing in books {o.counts?.missing_in_books ?? 0}. ITC in books {inr(o.itc_books, { paise: false })}, in 2B {inr(o.itc_2b, { paise: false })}.</>);
    case "ims":
      return o.nothing_to_approve ? line("Every record already has a decision.") : (
        <div className="mt-1 text-[13px] text-ink-2">
          {o.undecided} undecided before {date(o.deadline)}: {Object.entries(o.recommended ?? {}).map(([k, v]: [string, any]) => `${v.count} ${k}`).join(", ")}.
          {o.to_reject?.length > 0 && <ul className="mt-1 list-disc pl-5">{o.to_reject.map((r: any, i: number) => <li key={i}>Reject {r.supplier} {r.number} — {r.reason}</li>)}</ul>}
        </div>
      );
    case "risk":
      return line(<>ITC at risk {inr(o.totals?.itc_at_risk, { paise: false })}; {o.urgent_before_filing?.length ?? 0} item(s) due before GSTR-3B on {date(o.gstr3b_due)}.</>);
    case "followups":
      return o.nothing_to_approve ? line("No supplier needs a new follow-up.") : (
        <ul className="mt-1 list-disc pl-5 text-[13px] text-ink-2">{o.drafted.map((d: any) => <li key={d.followup_id}>{d.supplier} — {inr(d.amount, { paise: false })} ({d.language.toUpperCase()})</li>)}</ul>
      );
    case "table4":
      return line(<>Net ITC to claim <b className="text-ink">{inr(o.net)}</b> from {o.included} of {o.records} GSTR-2B records.</>);
    case "summary":
      return <pre className="mt-2 font-sans text-[13px] whitespace-pre-wrap text-ink">{o.text}</pre>;
    default:
      return null;
  }
}
