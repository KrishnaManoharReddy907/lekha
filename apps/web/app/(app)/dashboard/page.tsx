"use client";

import { useQuery } from "@tanstack/react-query";
import { ArrowRight, CalendarDays, Download, FileCheck2, FileSpreadsheet, FileText, Inbox, UploadCloud } from "lucide-react";
import Link from "next/link";
import { ItcTrend, type TrendMonth } from "@/components/itc-trend";
import { Badge, ButtonLink, Card, CardHeader, Empty, PageHeader, Stat, cx } from "@/components/ui";
import { api } from "@/lib/api";
import { useClients } from "@/lib/clients";
import { date, inr, inrShort, period as fmtPeriod } from "@/lib/format";
import { RISK_LABEL, usePeriods } from "@/lib/gst";
import { useSession } from "@/lib/session";
import type { Invoice, Page } from "@/lib/types";

interface FirmBoard {
  clients: { client_id: string; name: string; itc_fy: string; at_risk: string; in_review: number; latest_2b: string | null; ims_undecided: number; close: { id: string; status: string; period: string } | null; next_due: string | null; stage: string }[];
  calendar: { date: string; what: string }[];
  totals: { itc_fy: string; at_risk: string; in_review: number };
}
interface ClientTrend {
  months: TrendMonth[];
  working_capital_trapped: string;
  deadlines: { id: string; title: string; amount: string; due: string; kind: string; invoice_id: string | null }[];
}

const STAGE: Record<string, { label: string; tone: "ok" | "warn" | "info" | "neutral" }> = {
  ready: { label: "Ready to file", tone: "ok" },
  awaiting_approval: { label: "Awaiting approval", tone: "warn" },
  in_progress: { label: "In progress", tone: "info" },
  no_2b: { label: "Waiting for GSTR-2B", tone: "neutral" },
};

export default function Dashboard() {
  const { me } = useSession();
  const { current, clients, loading, setCurrent } = useClients();
  const { period } = usePeriods(current?.id);
  const firm = useQuery({ queryKey: ["dash-firm"], enabled: clients.length > 0, queryFn: () => api<FirmBoard>("/api/dashboard/firm") });
  const trend = useQuery({ queryKey: ["dash-client", current?.id], enabled: !!current, queryFn: () => api<ClientTrend>("/api/dashboard/client", { params: { client_id: current?.id } }) });
  const inv = useQuery({
    queryKey: ["invoices", current?.id, "dash"],
    enabled: !!current,
    queryFn: () => api<Page<Invoice>>("/api/invoices", { params: { client_id: current?.id, status: "review", limit: 200 } }),
  });
  const review = inv.data?.items ?? [];

  if (!loading && clients.length === 0)
    return (
      <>
        <PageHeader title={`Welcome, ${me?.user.name.split(" ")[0]}`} sub="Three steps to your first protected tax credit." />
        <div className="grid gap-4 md:grid-cols-3">
          {[
            ["1", me?.firm?.kind === "business" ? "Add your business and GSTIN" : "Add a client and their GSTIN", "/clients?new=1", "Add client"],
            ["2", "Upload purchase invoices — PDFs or phone photos", "/inbox", "Open inbox"],
            ["3", "Import GSTR-2B and let Lekha run the month's close", "/reconciliation", "Reconciliation"],
          ].map(([n, t, href, cta]) => (
            <Card key={n}>
              <div className="num mb-3 grid size-8 place-items-center rounded-full bg-accent-soft font-semibold text-accent">{n}</div>
              <p className="mb-4 font-medium">{t}</p>
              <ButtonLink href={href} variant={n === "1" ? "primary" : "secondary"} size="sm">{cta}</ButtonLink>
            </Card>
          ))}
        </div>
      </>
    );

  const showFirm = (firm.data?.clients.length ?? 0) > 1;
  return (
    <>
      <PageHeader
        eyebrow={me?.firm?.name}
        title={showFirm ? "Firm overview" : current?.name ?? "Dashboard"}
        sub={showFirm ? "Where every client stands this month." : "Tax credit status."}
        actions={<ButtonLink href="/inbox" variant="primary" icon={<UploadCloud className="size-4" />}>Upload invoices</ButtonLink>}
      />

      {showFirm && firm.data && (
        <div className="mb-8 grid gap-5 xl:grid-cols-[1fr_320px]">
          <Card pad={false}>
            <div className="grid grid-cols-3 border-b border-rule">
              {[["ITC claimed this FY", inrShort(firm.data.totals.itc_fy)], ["Credit at risk", inrShort(firm.data.totals.at_risk)], ["Invoices to review", String(firm.data.totals.in_review)]].map(([l, v]) => (
                <div key={l} className="border-r border-rule px-5 py-4 last:border-r-0">
                  <div className="text-[11.5px] font-medium tracking-wide text-ink-3 uppercase">{l}</div>
                  <div className="num mt-1 text-[20px] font-semibold">{v}</div>
                </div>
              ))}
            </div>
            <div className="overflow-x-auto">
              <table className="w-full min-w-[680px] text-[13px]">
                <thead><tr className="text-left text-[12px] text-ink-3"><th className="px-5 py-2 font-medium">Client</th><th className="px-3 py-2 font-medium">This month</th><th className="px-3 py-2 text-right font-medium">At risk</th><th className="px-3 py-2 text-right font-medium">Review</th><th className="px-3 py-2 font-medium">Next deadline</th></tr></thead>
                <tbody>
                  {firm.data.clients.map((c) => (
                    <tr key={c.client_id} className={cx("border-t border-rule hover:bg-sunk/60", c.client_id === current?.id && "bg-accent-soft/40")}>
                      <td className="px-5 py-2.5"><button className="text-left font-medium hover:text-accent" onClick={() => setCurrent(c.client_id)}>{c.name}</button><div className="text-[11.5px] text-ink-3">Latest GSTR-2B {fmtPeriod(c.latest_2b)}</div></td>
                      <td className="px-3 py-2.5">
                        <Badge tone={STAGE[c.stage]?.tone ?? "neutral"}>{STAGE[c.stage]?.label ?? c.stage}</Badge>
                        {c.ims_undecided > 0 && <div className="mt-0.5 text-[11.5px] text-ink-3">{c.ims_undecided} IMS undecided</div>}
                      </td>
                      <td className="num px-3 py-2.5 text-right">{inr(c.at_risk, { paise: false })}</td>
                      <td className="num px-3 py-2.5 text-right">{c.in_review}</td>
                      <td className="px-3 py-2.5 text-ink-2">{date(c.next_due)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
          <Card>
            <CardHeader title="GST calendar" sub="This month" action={<CalendarDays className="size-4 text-ink-3" />} />
            <ul className="flex flex-col gap-2.5">
              {firm.data.calendar.map((e) => {
                const past = new Date(e.date).getTime() < Date.now() - 86400000;
                return (
                  <li key={e.date + e.what} className={cx("flex gap-3 text-[13px]", past && "opacity-50")}>
                    <span className="num w-12 shrink-0 font-semibold">{new Date(e.date).getDate()} {new Date(e.date).toLocaleString("en-IN", { month: "short" })}</span>
                    <span>{e.what}</span>
                  </li>
                );
              })}
            </ul>
          </Card>
        </div>
      )}

      {current && (
        <>
          {showFirm && <h2 className="mb-4 text-[17px] font-semibold">{current.name}</h2>}
          <div className="mb-5 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
            <Stat label="Working capital trapped" value={inrShort(trend.data?.working_capital_trapped)} sub="ITC at risk right now" tone={Number(trend.data?.working_capital_trapped ?? 0) > 0 ? "bad" : "ok"} />
            <Stat label="Awaiting review" value={review.length} sub={inrShort(review.reduce((s, i) => s + Number(i.itc), 0)) + " ITC"} tone={review.length ? "warn" : undefined} />
            <Stat label="Deadlines in 30 days" value={trend.data?.deadlines.length ?? 0} sub={inrShort((trend.data?.deadlines ?? []).reduce((s, d) => s + Number(d.amount), 0))} tone={(trend.data?.deadlines.length ?? 0) ? "warn" : undefined} />
            <Stat label="Fraud & anomaly flags" value={review.filter((i) => i.flags?.length).length} sub="In the review queue" />
          </div>
          <div className="grid gap-5 lg:grid-cols-[1.4fr_1fr]">
            <Card>
              <CardHeader title="Input tax credit, last 12 months" sub="By invoice month: matched in GSTR-2B vs still missing" />
              {trend.data ? <ItcTrend months={trend.data.months} /> : <div className="h-60" />}
            </Card>
            <Card>
              <CardHeader title="Coming up" sub="Act before these dates" action={<Link href="/risk" className="text-[13px] text-accent">All risks</Link>} />
              {(trend.data?.deadlines ?? []).length === 0 ? (
                <Empty title="Nothing due in 30 days" />
              ) : (
                <ul className="divide-y divide-rule">
                  {trend.data!.deadlines.map((d) => (
                    <li key={d.id} className="flex items-center justify-between gap-3 py-2 text-[13px]">
                      <span className="min-w-0">
                        <span className="block truncate font-medium">{d.invoice_id ? <Link href={`/review/${d.invoice_id}`} className="hover:text-accent">{d.title}</Link> : d.title}</span>
                        <span className="text-[11.5px] text-ink-3">{RISK_LABEL[d.kind] ?? d.kind} · {date(d.due)}</span>
                      </span>
                      <span className="num shrink-0">{inr(d.amount, { paise: false })}</span>
                    </li>
                  ))}
                </ul>
              )}
            </Card>
            <Card>
              <CardHeader title="Review queue" sub="Lowest confidence first" action={review.length > 0 && <ButtonLink href="/review" size="sm" icon={<FileCheck2 className="size-3.5" />}>Start reviewing</ButtonLink>} />
              {review.length === 0 ? (
                <Empty icon={<Inbox className="size-7" />} title="All caught up">New uploads land here after Lekha reads them.</Empty>
              ) : (
                <ul className="divide-y divide-rule">
                  {[...review].sort((a, b) => (a.confidence ?? 0) - (b.confidence ?? 0)).slice(0, 5).map((i) => (
                    <li key={i.id}>
                      <Link href={`/review/${i.id}`} className="flex items-center justify-between gap-3 py-2.5 hover:text-accent">
                        <span className="min-w-0"><span className="block truncate font-medium">{i.supplier_name || "Unknown supplier"}</span><span className="num text-[12px] text-ink-3">{i.number || "—"}</span></span>
                        <span className="num shrink-0 text-[13px]">{inr(i.itc)}</span>
                        <ArrowRight className="size-4 shrink-0 text-ink-3" />
                      </Link>
                    </li>
                  ))}
                </ul>
              )}
            </Card>
            <Card>
              <CardHeader title="Exports" sub={period ? `For ${fmtPeriod(period)}` : "Import a GSTR-2B to enable period exports"} />
              <div className="grid gap-2 sm:grid-cols-2">
                {[
                  ["Tally XML", "Purchase vouchers + ledgers", `/api/exports/tally?client_id=${current.id}${period ? `&period=${period}` : ""}`, <FileText key="t" className="size-4" />],
                  ["Excel workbook", "Register, reconciliation, risk", period ? `/api/exports/workbook.xlsx?client_id=${current.id}&period=${period}` : "", <FileSpreadsheet key="x" className="size-4" />],
                  ["Client summary PDF", "One page for the owner", period ? `/api/exports/summary.pdf?client_id=${current.id}&period=${period}` : "", <Download key="p" className="size-4" />],
                  ["Register CSV", "All invoices", `/api/exports/register.csv?client_id=${current.id}`, <Download key="c" className="size-4" />],
                ].map(([t, s, href, icon]) => (
                  href ? (
                    <a key={t as string} href={href as string} className="flex items-start gap-2.5 rounded-md border border-rule p-3 hover:border-accent hover:bg-accent-soft/40">
                      <span className="mt-0.5 text-accent">{icon}</span>
                      <span><span className="block text-[13.5px] font-medium">{t}</span><span className="text-[12px] text-ink-3">{s}</span></span>
                    </a>
                  ) : null
                ))}
              </div>
            </Card>
          </div>
        </>
      )}
    </>
  );
}
