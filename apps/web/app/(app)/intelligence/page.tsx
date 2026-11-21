"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Network, RefreshCw } from "lucide-react";
import { toast } from "sonner";
import { Badge, Button, Card, Empty, PageHeader } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { inr } from "@/lib/format";
import { useSession } from "@/lib/session";

interface Signal {
  id: string;
  kind: "cycle" | "shell" | "template_reuse" | "cross_client";
  score: number;
  title: string;
  detail: string;
  members: string[];
  amount: string;
  status: string;
  evidence: { edges?: { from: string; to: string; value: string }[]; reasons?: string[] };
}

const KIND: Record<Signal["kind"], string> = {
  cycle: "Circular trading",
  shell: "Shell supplier",
  template_reuse: "Shared invoice template",
  cross_client: "Cross-client warning",
};

export default function IntelligencePage() {
  const { can } = useSession();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["signals"], queryFn: () => api<Signal[]>("/api/signals") });
  const run = useMutation({
    mutationFn: () => api("/api/intelligence/run", { method: "POST" }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["signals"] }); toast.success("Analysis complete"); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const setStatus = useMutation({
    mutationFn: ({ id, status }: { id: string; status: string }) => api(`/api/signals/${id}/status`, { body: { status } }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["signals"] }),
  });
  const all = q.data ?? [];
  const signals = all.filter((s) => s.kind !== "cross_client");
  const cross = all.filter((s) => s.kind === "cross_client");
  return (
    <>
      <PageHeader
        title="Fraud signals"
        sub="Patterns across all your clients' purchases that a single client could never see: circular trading, shell suppliers, invoice templates shared between 'different' businesses."
        actions={can("write") && <Button icon={<RefreshCw className="size-4" />} onClick={() => run.mutate()} loading={run.isPending}>Analyse now</Button>}
      />
      {all.length === 0 && !q.isLoading ? (
        <Empty icon={<Network className="size-8" />} title="No suspicious patterns">Lekha re-analyses the supply network after every reconciliation.</Empty>
      ) : (
        <div className="grid gap-4 lg:grid-cols-2">
          {signals.map((s) => (
            <Card key={s.id}>
              <div className="mb-2 flex flex-wrap items-center gap-2">
                <Badge tone={s.score >= 0.7 ? "bad" : "warn"}>{KIND[s.kind]}</Badge>
                <span className="num text-[12px] text-ink-3">risk {Math.round(s.score * 100)}%</span>
                <span className="num ml-auto text-[13px] font-medium">{inr(s.amount, { paise: false })}</span>
              </div>
              <h3 className="text-[15px] font-semibold">{s.title}</h3>
              <p className="mt-1 text-[13px] text-ink-2">{s.detail}</p>
              {s.kind === "cycle" && s.evidence.edges && <CycleDiagram edges={s.evidence.edges} />}
              {s.kind !== "cycle" && (
                <div className="mt-3 flex flex-wrap gap-1">
                  {s.members.map((m) => <span key={m} className="num rounded bg-sunk px-1.5 py-0.5 text-[11.5px]">{m}</span>)}
                </div>
              )}
              {can("write") && (
                <div className="mt-4 flex gap-2 border-t border-rule pt-3">
                  <Button size="sm" variant="danger" onClick={() => setStatus.mutate({ id: s.id, status: "confirmed" })}>Confirm & hold credit</Button>
                  <Button size="sm" variant="ghost" onClick={() => setStatus.mutate({ id: s.id, status: "dismissed" })}>Dismiss — genuine</Button>
                </div>
              )}
            </Card>
          ))}
        </div>
      )}
      {cross.length > 0 && (
        <Card className="mt-5" pad={false}>
          <div className="border-b border-rule px-5 py-4">
            <h3 className="text-[15px] font-semibold">Cross-client supplier warnings</h3>
            <p className="text-[13px] text-ink-3">Suppliers with open GST problems for one client who also sell to your other clients.</p>
          </div>
          <ul className="divide-y divide-rule">
            {cross.map((s) => (
              <li key={s.id} className="flex flex-wrap items-center justify-between gap-3 px-5 py-3 text-[13px]">
                <span className="min-w-0">
                  <span className="font-medium">{s.title}</span>
                  <span className="block text-ink-3">{s.detail}</span>
                </span>
                <span className="flex items-center gap-3">
                  <span className="num font-medium">{inr(s.amount, { paise: false })}</span>
                  {can("write") && <Button size="sm" variant="ghost" onClick={() => setStatus.mutate({ id: s.id, status: "dismissed" })}>Dismiss</Button>}
                </span>
              </li>
            ))}
          </ul>
        </Card>
      )}
    </>
  );
}

/** Small circular diagram of the money loop. */
function CycleDiagram({ edges }: { edges: { from: string; to: string; value: string }[] }) {
  const nodes = edges.map((e) => e.from);
  const n = nodes.length;
  const R = 70;
  const cx = 110;
  const cy = 92;
  const pos = nodes.map((_, i) => {
    const a = (i / n) * Math.PI * 2 - Math.PI / 2;
    return [cx + R * Math.cos(a), cy + R * Math.sin(a)];
  });
  return (
    <svg viewBox="0 0 220 190" className="mt-3 h-44 w-full max-w-sm" role="img" aria-label="Money flows in a loop between these GSTINs">
      <defs>
        <marker id="arr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
          <path d="M0,0 L10,5 L0,10 z" fill="var(--red)" />
        </marker>
      </defs>
      {edges.map((e, i) => {
        const [x1, y1] = pos[i];
        const [x2, y2] = pos[(i + 1) % n];
        const dx = x2 - x1;
        const dy = y2 - y1;
        const len = Math.hypot(dx, dy);
        const ox = (dx / len) * 16;
        const oy = (dy / len) * 16;
        return (
          <g key={i}>
            <line x1={x1 + ox} y1={y1 + oy} x2={x2 - ox} y2={y2 - oy} stroke="var(--red)" strokeWidth="1.6" markerEnd="url(#arr)" />
            <text x={(x1 + x2) / 2} y={(y1 + y2) / 2 - 4} textAnchor="middle" fontSize="9" fill="var(--ink-2)">₹{(Number(e.value) / 1e5).toFixed(1)}L</text>
          </g>
        );
      })}
      {nodes.map((g, i) => (
        <g key={g}>
          <circle cx={pos[i][0]} cy={pos[i][1]} r="14" fill="var(--sheet)" stroke="var(--ink-3)" />
          <text x={pos[i][0]} y={pos[i][1] + 3} textAnchor="middle" fontSize="8" fill="var(--ink)" fontFamily="var(--font-mono)">{g.slice(0, 2)}·{g.slice(2, 5)}</text>
        </g>
      ))}
    </svg>
  );
}
