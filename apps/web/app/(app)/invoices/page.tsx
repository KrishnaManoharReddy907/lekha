"use client";

import { useQuery } from "@tanstack/react-query";
import { Search } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { Badge, ConfidenceMeter, Empty, Input, PageHeader, Select, StatusBadge, Table, Td, Th } from "@/components/ui";
import { api } from "@/lib/api";
import { useClients } from "@/lib/clients";
import { date, inr, period } from "@/lib/format";
import type { Invoice, Page } from "@/lib/types";

export default function InvoicesPage() {
  const { current } = useClients();
  const [status, setStatus] = useState("");
  const [q, setQ] = useState("");
  const [offset, setOffset] = useState(0);
  const limit = 50;
  const list = useQuery({
    queryKey: ["invoices", current?.id, status, q, offset],
    enabled: !!current,
    queryFn: () => api<Page<Invoice>>("/api/invoices", { params: { client_id: current?.id, status, q, limit, offset } }),
    placeholderData: (prev) => prev,
  });
  const items = list.data?.items ?? [];
  const totalItc = items.filter((i) => i.status === "approved").reduce((s, i) => s + Number(i.itc), 0);

  return (
    <>
      <PageHeader
        title="Purchase register"
        sub={current ? <>Every purchase invoice for <span className="font-medium text-ink">{current.name}</span>, with its tax credit.</> : undefined}
      />
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <div className="relative w-full max-w-xs">
          <Search className="pointer-events-none absolute top-2.5 left-2.5 size-4 text-ink-3" />
          <Input aria-label="Search invoices" placeholder="Number, supplier or GSTIN" value={q} onChange={(e) => { setQ(e.target.value); setOffset(0); }} className="pl-8" />
        </div>
        <Select aria-label="Status" value={status} onChange={(e) => { setStatus(e.target.value); setOffset(0); }} className="w-44">
          <option value="">All statuses</option>
          <option value="review">Needs review</option>
          <option value="approved">Approved</option>
          <option value="rejected">Rejected</option>
          <option value="duplicate">Duplicate</option>
        </Select>
        <span className="ml-auto text-[13px] text-ink-3">
          {list.data?.total ?? 0} invoices · approved ITC on this page <span className="num font-medium text-ink">{inr(totalItc)}</span>
        </span>
      </div>
      {items.length === 0 && !list.isLoading ? (
        <Empty title="No invoices match">Upload bills in the Inbox, or change the filters.</Empty>
      ) : (
        <Table>
          <thead>
            <tr>
              <Th>Date</Th>
              <Th>Number</Th>
              <Th>Supplier</Th>
              <Th>Period</Th>
              <Th right>Taxable</Th>
              <Th right>ITC</Th>
              <Th>Status</Th>
              <Th>Confidence</Th>
            </tr>
          </thead>
          <tbody>
            {items.map((i) => (
              <tr key={i.id} className="hover:bg-sunk/60">
                <Td className="whitespace-nowrap">{date(i.invoice_date)}</Td>
                <Td mono>
                  <Link href={`/review/${i.id}`} className="text-accent hover:underline">{i.number || "—"}</Link>
                  {i.doc_type !== "invoice" && <Badge className="ml-1.5" tone="info">{i.doc_type.replace("_", " ")}</Badge>}
                </Td>
                <Td className="max-w-[260px]">
                  <div className="truncate">{i.supplier_name || "—"}</div>
                  <div className="num text-[12px] text-ink-3">{i.supplier_gstin}</div>
                </Td>
                <Td>{period(i.period)}</Td>
                <Td right mono>{inr(i.taxable_value)}</Td>
                <Td right mono>
                  {inr(i.itc)}
                  {Number(i.blocked_amount) > 0 && <div className="text-[11.5px] text-amber">{inr(i.blocked_amount)} blocked</div>}
                </Td>
                <Td>
                  <div className="flex flex-wrap items-center gap-1">
                    <StatusBadge status={i.status} />
                    {i.flags?.length > 0 && <Badge tone="warn" title={i.flags.map((f) => f.message).join("\n")}>{i.flags.length} flag{i.flags.length > 1 ? "s" : ""}</Badge>}
                  </div>
                </Td>
                <Td><ConfidenceMeter value={i.confidence} /></Td>
              </tr>
            ))}
          </tbody>
        </Table>
      )}
      {(list.data?.total ?? 0) > limit && (
        <div className="mt-3 flex justify-end gap-2 text-[13px]">
          <button disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - limit))} className="text-accent disabled:text-ink-3">Previous</button>
          <span className="text-ink-3">{offset + 1}–{Math.min(offset + limit, list.data!.total)} of {list.data!.total}</span>
          <button disabled={offset + limit >= (list.data?.total ?? 0)} onClick={() => setOffset(offset + limit)} className="text-accent disabled:text-ink-3">Next</button>
        </div>
      )}
    </>
  );
}
