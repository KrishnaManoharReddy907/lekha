"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FlaskConical, Plus, Rocket } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { Dialog } from "@/components/dialog";
import { Badge, Button, Card, CardHeader, Field, Input, PageHeader, Textarea, cx } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { date, inr } from "@/lib/format";
import { useSession } from "@/lib/session";

interface Version { id: string; version: number; status: string; title: string; source: string; effective_from: string; effective_to: string | null; params: Record<string, unknown>; firm_scoped: boolean }
interface Rule { code: string; title: string; current_version: number | null; versions: Version[] }
interface Report { id: string; status: string; summary: { invoices_checked: number; invoices_affected: number; new_findings: number; cleared_findings: number; risk_change: string; effective_from: string }; changes: { invoice_id: string; number: string; supplier: string; client: string; new_findings: string[]; cleared_findings: string[]; risk_before: string; risk_after: string }[] }

export default function RulesPage() {
  const { can } = useSession();
  const qc = useQueryClient();
  const rules = useQuery({ queryKey: ["rules"], queryFn: () => api<Rule[]>("/api/rules") });
  const [draftFor, setDraftFor] = useState<Rule | null>(null);
  const [report, setReport] = useState<string | null>(null);
  const rep = useQuery({
    queryKey: ["impact", report],
    enabled: !!report,
    queryFn: () => api<Report>(`/api/rules/impact/${report}`),
    refetchInterval: (q) => (q.state.data?.status === "running" ? 1200 : false),
  });
  const simulate = useMutation({
    mutationFn: (id: string) => api<{ id: string }>("/api/rules/impact", { body: { draft_ids: [id] } }),
    onSuccess: (r) => setReport(r.id),
    onError: (e) => toast.error(errorMessage(e)),
  });
  const publish = useMutation({
    mutationFn: (id: string) => api(`/api/rules/drafts/${id}/publish`, { method: "POST" }),
    onSuccess: () => { toast.success("Published — every client is being re-checked"); qc.invalidateQueries({ queryKey: ["rules"] }); setReport(null); },
    onError: (e) => toast.error(errorMessage(e)),
  });

  return (
    <>
      <PageHeader
        title="GST rules"
        sub="Every rate, deadline and threshold Lekha uses — with the date it applies from. Each invoice is checked under the rules of its own date. Draft a change and see its impact before publishing."
      />
      <div className="grid gap-5 xl:grid-cols-[1fr_420px]">
        <div className="flex flex-col gap-3">
          {rules.data?.map((r) => (
            <Card key={r.code}>
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <div className="num text-[12px] text-ink-3">{r.code}</div>
                  <div className="font-medium">{r.title}</div>
                </div>
                {can("admin") && <Button size="sm" icon={<Plus className="size-3.5" />} onClick={() => setDraftFor(r)}>Draft change</Button>}
              </div>
              <ol className="mt-3 flex flex-col gap-1.5 border-l-2 border-rule pl-3">
                {r.versions.slice().reverse().map((v) => (
                  <li key={v.id} className="text-[12.5px]">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="num">v{v.version}</span>
                      <span className="text-ink-2">{date(v.effective_from)} → {v.effective_to ? date(v.effective_to) : "now"}</span>
                      {v.version === r.current_version && <Badge tone="ok">In force</Badge>}
                      {v.status === "draft" && <Badge tone="warn">Draft</Badge>}
                      {v.firm_scoped && v.status !== "draft" && <Badge tone="info">Your workspace</Badge>}
                    </div>
                    <code className="mt-0.5 block truncate text-[11.5px] text-ink-3">{JSON.stringify(v.params)}</code>
                    {v.source && <div className="text-[11.5px] text-ink-3">Source: {v.source}</div>}
                    {v.status === "draft" && can("admin") && (
                      <div className="mt-1.5 flex gap-2">
                        <Button size="sm" icon={<FlaskConical className="size-3.5" />} loading={simulate.isPending} onClick={() => simulate.mutate(v.id)}>Simulate impact</Button>
                        <Button size="sm" variant="primary" icon={<Rocket className="size-3.5" />} onClick={() => publish.mutate(v.id)}>Publish</Button>
                      </div>
                    )}
                  </li>
                ))}
              </ol>
            </Card>
          ))}
        </div>
        <div className="xl:sticky xl:top-6 xl:self-start">
          <Card>
            <CardHeader title="Impact simulation" sub="What would change if the draft were published" />
            {!rep.data ? <p className="text-[13px] text-ink-3">Draft a change, then press “Simulate impact”.</p> : rep.data.status === "running" ? <p className="text-[13px]">Re-checking every invoice…</p> : (
              <>
                <div className="grid grid-cols-2 gap-2">
                  {[["Invoices checked", rep.data.summary.invoices_checked], ["Affected", rep.data.summary.invoices_affected], ["New findings", rep.data.summary.new_findings], ["Credit at risk change", inr(rep.data.summary.risk_change, { paise: false, sign: true })]].map(([l, v]) => (
                    <div key={l as string} className="rounded-md bg-sunk p-2.5"><div className="text-[11px] text-ink-3 uppercase">{l}</div><div className="num font-semibold">{v}</div></div>
                  ))}
                </div>
                <ul className="mt-3 max-h-[50vh] divide-y divide-rule overflow-y-auto text-[12.5px]">
                  {rep.data.changes.map((c) => (
                    <li key={c.invoice_id} className="py-2">
                      <div className="font-medium">{c.supplier} · <span className="num">{c.number}</span></div>
                      <div className="text-ink-3">{c.client}</div>
                      {c.new_findings.map((f) => <Badge key={f} tone="bad" className="mr-1">+ {f}</Badge>)}
                      {c.cleared_findings.map((f) => <Badge key={f} tone="ok" className="mr-1">− {f}</Badge>)}
                      {c.risk_before !== c.risk_after && <span className={cx("num ml-1", Number(c.risk_after) > Number(c.risk_before) ? "text-red" : "text-ok")}>{inr(c.risk_before, { paise: false })} → {inr(c.risk_after, { paise: false })}</span>}
                    </li>
                  ))}
                </ul>
              </>
            )}
          </Card>
        </div>
      </div>
      <DraftDialog rule={draftFor} onClose={() => setDraftFor(null)} onCreated={() => qc.invalidateQueries({ queryKey: ["rules"] })} />
    </>
  );
}

function DraftDialog({ rule, onClose, onCreated }: { rule: Rule | null; onClose: () => void; onCreated: () => void }) {
  const latest = rule?.versions[rule.versions.length - 1];
  const [params, setParams] = useState("");
  const [from, setFrom] = useState(new Date().toISOString().slice(0, 10));
  const [source, setSource] = useState("");
  const m = useMutation({
    mutationFn: () => api("/api/rules/drafts", { body: { code: rule!.code, params: JSON.parse(params), effective_from: from, source } }),
    onSuccess: () => { toast.success("Draft saved — simulate it before publishing"); onCreated(); onClose(); },
    onError: (e) => toast.error(e instanceof SyntaxError ? "Parameters must be valid JSON." : errorMessage(e)),
  });
  return (
    <Dialog open={!!rule} onOpenChange={(o) => { if (!o) onClose(); else setParams(JSON.stringify(latest?.params ?? {}, null, 2)); }} title={`Draft change · ${rule?.code ?? ""}`} description={rule?.title} wide>
      <form className="flex flex-col gap-4" onSubmit={(e) => { e.preventDefault(); m.mutate(); }} onFocus={() => { if (!params && latest) setParams(JSON.stringify(latest.params, null, 2)); }}>
        <Field label="Parameters (JSON)" htmlFor="r-params" hint="Start from the current values and change what the new notification says.">
          <Textarea id="r-params" rows={8} className="font-mono text-[12.5px]" value={params || JSON.stringify(latest?.params ?? {}, null, 2)} onChange={(e) => setParams(e.target.value)} />
        </Field>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Effective from" htmlFor="r-from"><Input id="r-from" type="date" required value={from} onChange={(e) => setFrom(e.target.value)} /></Field>
          <Field label="Source" htmlFor="r-src" hint="Notification / circular number"><Input id="r-src" value={source} onChange={(e) => setSource(e.target.value)} /></Field>
        </div>
        <Button type="submit" variant="primary" loading={m.isPending}>Save draft</Button>
      </form>
    </Dialog>
  );
}

