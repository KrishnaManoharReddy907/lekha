"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Banknote } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { toast } from "sonner";
import { Dialog } from "@/components/dialog";
import { Badge, Button, Empty, Field, Input, PageHeader, Select, Stat, Table, Td, Th } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { useClients } from "@/lib/clients";
import { date, inr, inrShort } from "@/lib/format";
import { useSession } from "@/lib/session";

interface Payable { invoice_id: string; number: string; supplier_name: string | null; supplier_gstin: string; date: string; total: string; unpaid: string; age_days: number; days_to_180: number; itc: string }

export default function PayablesPage() {
  const { current } = useClients();
  const { can } = useSession();
  const qc = useQueryClient();
  const [pay, setPay] = useState<Payable | null>(null);
  const q = useQuery({ queryKey: ["payables", current?.id], enabled: !!current, queryFn: () => api<Payable[]>("/api/payables", { params: { client_id: current?.id } }) });
  const items = q.data ?? [];
  const total = items.reduce((s, p) => s + Number(p.unpaid), 0);
  const urgent = items.filter((p) => p.days_to_180 <= 30);
  return (
    <>
      <PageHeader title="Payables" sub="Unpaid purchase bills, ordered by how soon unpaid ITC must be reversed (Rule 37: 180 days)." />
      {items.length === 0 ? (
        <Empty icon={<Banknote className="size-8" />} title="Nothing unpaid">All approved invoices have been paid.</Empty>
      ) : (
        <>
          <div className="mb-5 grid gap-3 sm:grid-cols-3">
            <Stat label="Unpaid" value={inrShort(total)} sub={`${items.length} bills`} />
            <Stat label="Due within 30 days" value={urgent.length} sub={inrShort(urgent.reduce((s, p) => s + Number(p.itc), 0)) + " ITC to protect"} tone={urgent.length ? "warn" : undefined} />
            <Stat label="Past 180 days" value={items.filter((p) => p.days_to_180 < 0).length} sub="Reverse ITC with interest" tone={items.some((p) => p.days_to_180 < 0) ? "bad" : undefined} />
          </div>
          <Table>
            <thead><tr><Th>Supplier</Th><Th>Invoice</Th><Th right>Unpaid</Th><Th right>ITC</Th><Th>180-day limit</Th><Th /></tr></thead>
            <tbody>
              {items.map((p) => (
                <tr key={p.invoice_id}>
                  <Td className="max-w-[240px]"><div className="truncate font-medium">{p.supplier_name}</div><div className="num text-[11.5px] text-ink-3">{p.supplier_gstin}</div></Td>
                  <Td><Link href={`/review/${p.invoice_id}`} className="num text-accent hover:underline">{p.number}</Link><div className="text-[11.5px] text-ink-3">{date(p.date)} · {p.age_days} days old</div></Td>
                  <Td right mono>{inr(p.unpaid)}</Td>
                  <Td right mono>{inr(p.itc)}</Td>
                  <Td>{p.days_to_180 < 0 ? <Badge tone="bad">{-p.days_to_180} d overdue</Badge> : p.days_to_180 <= 30 ? <Badge tone="warn">{p.days_to_180} d left</Badge> : <span className="text-ink-3">{p.days_to_180} d left</span>}</Td>
                  <Td right>{can("write") && <Button size="sm" onClick={() => setPay(p)}>Record payment</Button>}</Td>
                </tr>
              ))}
            </tbody>
          </Table>
        </>
      )}
      <PayDialog p={pay} onClose={() => setPay(null)} onDone={() => { qc.invalidateQueries({ queryKey: ["payables"] }); qc.invalidateQueries({ queryKey: ["risk"] }); }} />
    </>
  );
}

function PayDialog({ p, onClose, onDone }: { p: Payable | null; onClose: () => void; onDone: () => void }) {
  const [amount, setAmount] = useState("");
  const [paidOn, setPaidOn] = useState(new Date().toISOString().slice(0, 10));
  const [mode, setMode] = useState("NEFT");
  const [ref, setRef] = useState("");
  const m = useMutation({
    mutationFn: () => api(`/api/invoices/${p!.invoice_id}/payments`, { body: { amount: amount || p!.unpaid, paid_on: paidOn, mode, reference: ref || null } }),
    onSuccess: () => { toast.success("Payment recorded — risk re-checked"); onDone(); onClose(); setAmount(""); setRef(""); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  return (
    <Dialog open={!!p} onOpenChange={(o) => !o && onClose()} title="Record payment" description={p ? `${p.supplier_name} · ${p.number} · unpaid ${inr(p.unpaid)}` : ""}>
      <form className="flex flex-col gap-4" onSubmit={(e) => { e.preventDefault(); m.mutate(); }}>
        <Field label="Amount" htmlFor="pay-amt" hint="Leave empty to pay the full unpaid amount"><Input id="pay-amt" inputMode="decimal" className="num" value={amount} onChange={(e) => setAmount(e.target.value)} placeholder={p?.unpaid} /></Field>
        <Field label="Paid on" htmlFor="pay-date"><Input id="pay-date" type="date" required value={paidOn} onChange={(e) => setPaidOn(e.target.value)} /></Field>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Mode" htmlFor="pay-mode"><Select id="pay-mode" value={mode} onChange={(e) => setMode(e.target.value)}>{["NEFT", "RTGS", "IMPS", "UPI", "Cheque", "Cash"].map((x) => <option key={x}>{x}</option>)}</Select></Field>
          <Field label="Reference" htmlFor="pay-ref"><Input id="pay-ref" value={ref} onChange={(e) => setRef(e.target.value)} /></Field>
        </div>
        <Button type="submit" variant="primary" loading={m.isPending}>Save payment</Button>
      </form>
    </Dialog>
  );
}
