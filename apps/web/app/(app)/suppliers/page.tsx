"use client";

import { useQuery } from "@tanstack/react-query";
import { Search, Truck } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { Grade, type SupplierRow } from "@/components/supplier-grade";
import { Badge, Empty, Input, PageHeader, Table, Td, Th } from "@/components/ui";
import { api } from "@/lib/api";
import { useClients } from "@/lib/clients";
import { inr } from "@/lib/format";

export default function SuppliersPage() {
  const { current } = useClients();
  const [scope, setScope] = useState<"client" | "firm">("client");
  const [q, setQ] = useState("");
  const list = useQuery({
    queryKey: ["suppliers", scope, current?.id, q],
    queryFn: () => api<SupplierRow[]>("/api/suppliers", { params: { client_id: scope === "client" ? current?.id : undefined, q } }),
  });
  const rows = list.data ?? [];
  return (
    <>
      <PageHeader title="Suppliers" sub="GST compliance of every supplier, learned across all your clients. Lowest scores first." />
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <div className="inline-flex rounded-md border border-rule-2 p-0.5 text-[13px]">
          {(["client", "firm"] as const).map((s) => (
            <button key={s} onClick={() => setScope(s)} className={`rounded px-3 py-1 ${scope === s ? "bg-accent-soft font-medium text-accent" : "text-ink-2"}`}>
              {s === "client" ? current?.name ?? "This client" : "All clients"}
            </button>
          ))}
        </div>
        <div className="relative w-full max-w-xs">
          <Search className="pointer-events-none absolute top-2.5 left-2.5 size-4 text-ink-3" />
          <Input aria-label="Search suppliers" placeholder="Name or GSTIN" value={q} onChange={(e) => setQ(e.target.value)} className="pl-8" />
        </div>
      </div>
      {rows.length === 0 && !list.isLoading ? (
        <Empty icon={<Truck className="size-8" />} title="No suppliers yet">Suppliers appear as invoices and GSTR-2B statements come in.</Empty>
      ) : (
        <Table>
          <thead><tr><Th>Supplier</Th><Th>Score</Th><Th right>On time</Th><Th right>Invoices</Th><Th right>ITC</Th><Th right>At risk</Th><Th /></tr></thead>
          <tbody>
            {rows.map((s) => (
              <tr key={s.id} className="hover:bg-sunk/60">
                <Td className="max-w-[280px]">
                  <Link href={`/suppliers/${s.id}`} className="font-medium hover:text-accent">{s.trade_name || s.legal_name || s.gstin}</Link>
                  <div className="num text-[11.5px] text-ink-3">{s.gstin}{s.msme_category && <Badge className="ml-1.5" tone="info">MSME · {s.msme_category}</Badge>}</div>
                </Td>
                <Td><Grade score={s.score} grade={s.score_detail?.grade} /></Td>
                <Td right mono>{s.score_detail?.on_time_rate !== undefined ? `${Math.round(s.score_detail.on_time_rate * 100)}%` : "—"}</Td>
                <Td right mono>{s.invoices}</Td>
                <Td right mono>{inr(s.itc, { paise: false })}</Td>
                <Td right mono className={Number(s.at_risk) > 0 ? "text-red" : "text-ink-3"}>{Number(s.at_risk) > 0 ? inr(s.at_risk, { paise: false }) : "—"}</Td>
                <Td right><Link href={`/suppliers/${s.id}`} className="text-[13px] text-accent">Open</Link></Td>
              </tr>
            ))}
          </tbody>
        </Table>
      )}
    </>
  );
}
