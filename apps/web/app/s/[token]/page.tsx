"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2 } from "lucide-react";
import { useParams } from "next/navigation";
import { useState } from "react";
import { Logo } from "@/components/logo";
import { Badge, Button, Input, Select, Spinner, Textarea } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { date, inr } from "@/lib/format";

interface Portal {
  firm_name: string;
  supplier_name: string;
  supplier_gstin: string;
  issues: { risk_id: string; kind: string; number: string; date: string | null; amount: string; buyer_name: string; buyer_gstin: string | null }[];
  responses: { item_ref: string; action: string; message: string; expected_by: string | null }[];
}

const WHAT: Record<string, { title: string; fix: string }> = {
  NOT_IN_2B: { title: "Not reported in your GSTR-1", fix: "Add this invoice to your next GSTR-1 / IFF (or amend if it went to a wrong GSTIN)." },
  VALUE_MISMATCH: { title: "Reported with lower tax", fix: "Amend the invoice in GSTR-1 (Table 9A) to the correct taxable value and tax." },
  WRONG_TAX_HEAD: { title: "IGST / CGST+SGST reported wrongly", fix: "Amend the place of supply and tax head in GSTR-1 (Table 9A)." },
  R37A_SUPPLIER_3B: { title: "Tax not paid in GSTR-3B", fix: "File GSTR-3B for the period so the tax is paid." },
};

/** Login-free page for a supplier: see exactly what to fix and reply in one click. */
export default function SupplierPortal() {
  const { token } = useParams<{ token: string }>();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["portal", token], queryFn: () => api<Portal>(`/api/public/supplier/${token}`), retry: false });

  if (q.isLoading) return <div className="grid min-h-dvh place-items-center"><Spinner className="size-6" /></div>;
  if (q.isError)
    return (
      <div className="mx-auto max-w-lg px-4 py-12"><Logo /><h1 className="mt-10 text-[22px] font-semibold">Link not valid</h1><p className="mt-2 text-ink-2">{errorMessage(q.error)}</p></div>
    );
  const p = q.data!;
  const replied = new Map(p.responses.map((r) => [r.item_ref, r]));
  return (
    <div className="mx-auto max-w-3xl px-[max(16px,env(safe-area-inset-left))] py-8">
      <Logo />
      <h1 className="mt-8 text-[24px] font-semibold">GST corrections requested by {p.firm_name}</h1>
      <p className="mt-2 text-ink-2">
        For <span className="font-medium text-ink">{p.supplier_name}</span> (<span className="num">{p.supplier_gstin}</span>). Your customers cannot claim input tax credit on these invoices until they appear correctly in their GSTR-2B.
      </p>
      {p.issues.length === 0 ? (
        <p className="mt-8 flex items-center gap-2 rounded-md bg-ok-soft p-4 text-ok"><CheckCircle2 className="size-5" /> Everything is resolved. Thank you!</p>
      ) : (
        <ul className="mt-8 flex flex-col gap-3">
          {p.issues.map((i) => <IssueCard key={i.risk_id} i={i} replied={replied.get(i.risk_id)} token={token} onDone={() => qc.invalidateQueries({ queryKey: ["portal", token] })} />)}
        </ul>
      )}
      <p className="mt-10 text-center text-[12px] text-ink-3">Sent via Lekha on behalf of {p.firm_name}. This page shows only invoices issued by you.</p>
    </div>
  );
}

function IssueCard({ i, replied, token, onDone }: { i: Portal["issues"][number]; replied?: Portal["responses"][number]; token: string; onDone: () => void }) {
  const [action, setAction] = useState("will_file");
  const [message, setMessage] = useState("");
  const [by, setBy] = useState("");
  const m = useMutation({
    mutationFn: () => api(`/api/public/supplier/${token}`, { body: { item_ref: i.risk_id, action, message, expected_by: by || null } }),
    onSuccess: onDone,
  });
  const w = WHAT[i.kind] ?? { title: i.kind, fix: "" };
  return (
    <li className="rounded-lg border border-rule bg-sheet p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <Badge tone="warn">{w.title}</Badge>
          <p className="mt-2 font-medium">Invoice <span className="num">{i.number}</span> · {date(i.date)}</p>
          <p className="text-[13px] text-ink-2">Billed to {i.buyer_name} <span className="num">{i.buyer_gstin}</span></p>
          <p className="mt-1 text-[13px] text-ink-3">How to fix: {w.fix}</p>
        </div>
        <div className="num text-right text-[17px] font-semibold">{inr(i.amount)}<div className="text-[11.5px] font-normal text-ink-3">credit held back</div></div>
      </div>
      {replied ? (
        <p className="mt-3 rounded-md bg-ok-soft px-3 py-2 text-[13px] text-ok">You replied: {replied.action.replace("_", " ")}{replied.expected_by ? ` by ${date(replied.expected_by)}` : ""}.</p>
      ) : (
        <form className="mt-3 grid gap-2 border-t border-rule pt-3 sm:grid-cols-[180px_160px_1fr_auto]" onSubmit={(e) => { e.preventDefault(); m.mutate(); }}>
          <Select aria-label="Your reply" value={action} onChange={(e) => setAction(e.target.value)}>
            <option value="will_file">I will file / fix it</option>
            <option value="filed">Already filed</option>
            <option value="amended">Already amended</option>
            <option value="disputed">This is not correct</option>
          </Select>
          <Input aria-label="By date" type="date" value={by} onChange={(e) => setBy(e.target.value)} />
          <Textarea aria-label="Message" rows={1} placeholder="Message (optional)" value={message} onChange={(e) => setMessage(e.target.value)} />
          <Button type="submit" variant="primary" loading={m.isPending}>Send</Button>
          {m.isError && <p className="text-[12.5px] text-red sm:col-span-4">{errorMessage(m.error)}</p>}
        </form>
      )}
    </li>
  );
}
