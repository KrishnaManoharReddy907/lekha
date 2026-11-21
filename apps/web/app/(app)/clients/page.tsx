"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Building2, Plus, Trash2 } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import { toast } from "sonner";
import { Dialog } from "@/components/dialog";
import { Badge, Button, Empty, Field, Input, PageHeader, Select, Table, Td, Th } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { useClients } from "@/lib/clients";
import { STATE_NAMES } from "@/lib/format";
import { gstinProblem, normaliseGstin } from "@/lib/gstin";
import { useSession } from "@/lib/session";
import type { Client } from "@/lib/types";

export default function ClientsPage() {
  return (
    <Suspense>
      <ClientsInner />
    </Suspense>
  );
}

function ClientsInner() {
  const { clients, loading, setCurrent } = useClients();
  const { can, me } = useSession();
  const params = useSearchParams();
  const [open, setOpen] = useState(false);
  useEffect(() => {
    if (params.get("new")) setOpen(true);
  }, [params]);
  const business = me?.firm?.kind === "business";

  return (
    <>
      <PageHeader
        title={business ? "Business & GSTINs" : "Clients"}
        sub={business ? "The GSTINs your purchase invoices are billed to." : "Businesses whose purchase invoices and GST you manage."}
        actions={can("write") && (
          <Button variant="primary" icon={<Plus className="size-4" />} onClick={() => setOpen(true)}>
            {business ? "Add GSTIN holder" : "Add client"}
          </Button>
        )}
      />
      {loading ? null : clients.length === 0 ? (
        <Empty
          icon={<Building2 className="size-8" />}
          title={business ? "Add your business to start" : "Add your first client"}
          action={can("write") && <Button variant="primary" onClick={() => setOpen(true)}>Add {business ? "business" : "client"}</Button>}
        >
          You need the business name and at least one GSTIN. Lekha checks the GSTIN as you type.
        </Empty>
      ) : (
        <Table>
          <thead>
            <tr>
              <Th>Name</Th>
              <Th>GSTIN(s)</Th>
              <Th>Filing</Th>
              <Th>Contact</Th>
              <Th />
            </tr>
          </thead>
          <tbody>
            {clients.map((c) => (
              <tr key={c.id} className="hover:bg-sunk/60">
                <Td>
                  <Link href={`/clients/${c.id}`} className="font-medium hover:text-accent" onClick={() => setCurrent(c.id)}>
                    {c.name}
                  </Link>
                  {c.trade_name ? <div className="text-[12px] text-ink-3">{c.trade_name}</div> : null}
                </Td>
                <Td>
                  <div className="flex flex-wrap gap-1">
                    {c.gstins.map((g) => (
                      <span key={g.id} className="num rounded bg-sunk px-1.5 py-0.5 text-[12px]" title={STATE_NAMES[g.state_code]}>
                        {g.gstin}
                      </span>
                    ))}
                    {c.gstins.length === 0 && <Badge tone="warn">No GSTIN</Badge>}
                  </div>
                </Td>
                <Td className="capitalize">{c.filing_frequency}</Td>
                <Td className="text-ink-2">{c.contact_name || c.contact_email || "—"}</Td>
                <Td right>
                  <Link href={`/clients/${c.id}`} className="text-[13px] text-accent" onClick={() => setCurrent(c.id)}>
                    Open
                  </Link>
                </Td>
              </tr>
            ))}
          </tbody>
        </Table>
      )}
      <NewClientDialog open={open} onOpenChange={setOpen} />
    </>
  );
}

function NewClientDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
  const qc = useQueryClient();
  const router = useRouter();
  const { setCurrent } = useClients();
  const [form, setForm] = useState({ name: "", trade_name: "", contact_name: "", contact_email: "", contact_phone: "", filing_frequency: "monthly", language: "en" });
  const [gstins, setGstins] = useState<string[]>([""]);
  const problems = gstins.map(gstinProblem);
  const m = useMutation({
    mutationFn: () =>
      api<Client>("/api/clients", {
        body: {
          ...Object.fromEntries(Object.entries(form).map(([k, v]) => [k, v === "" ? null : v])),
          name: form.name,
          gstins: gstins.filter((g) => g.trim()).map((g) => ({ gstin: normaliseGstin(g) })),
        },
      }),
    onSuccess: (c) => {
      qc.invalidateQueries({ queryKey: ["clients"] });
      setCurrent(c.id);
      toast.success(`${c.name} added`);
      onOpenChange(false);
      setForm({ ...form, name: "", trade_name: "", contact_name: "", contact_email: "", contact_phone: "" });
      setGstins([""]);
      router.replace("/clients");
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const valid = form.name.trim() && gstins.some((g) => g.trim()) && problems.every((p) => !p);

  return (
    <Dialog open={open} onOpenChange={onOpenChange} title="Add client" description="Lekha uses the GSTIN to know which invoices belong to this business." wide>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          if (valid) m.mutate();
        }}
        className="grid gap-4 sm:grid-cols-2"
      >
        <div className="sm:col-span-2">
          <Field label="Legal name" htmlFor="c-name">
            <Input id="c-name" required value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="Deccan FurnitureWorks Pvt Ltd" />
          </Field>
        </div>
        <div className="flex flex-col gap-2 sm:col-span-2">
          <span className="text-[13px] font-medium text-ink-2">GSTIN</span>
          {gstins.map((g, i) => {
            const n = normaliseGstin(g);
            return (
              <div key={i} className="flex items-start gap-2">
                <div className="flex-1">
                  <Input
                    aria-label={`GSTIN ${i + 1}`}
                    value={g}
                    invalid={!!problems[i]}
                    onChange={(e) => setGstins(gstins.map((x, j) => (j === i ? e.target.value.toUpperCase() : x)))}
                    placeholder="36AAGCD7781Q1ZR"
                    className="num tracking-wide"
                    maxLength={20}
                  />
                  <p className={`mt-1 text-[12px] ${problems[i] ? "text-red" : "text-ink-3"}`}>
                    {problems[i] ?? (n.length === 15 ? `Valid · ${STATE_NAMES[n.slice(0, 2)] ?? "state " + n.slice(0, 2)} · PAN ${n.slice(2, 12)}` : "15 characters, from the GST certificate")}
                  </p>
                </div>
                {gstins.length > 1 && (
                  <Button type="button" variant="ghost" size="sm" onClick={() => setGstins(gstins.filter((_, j) => j !== i))} aria-label="Remove GSTIN">
                    <Trash2 className="size-4" />
                  </Button>
                )}
              </div>
            );
          })}
          <button type="button" className="self-start text-[13px] text-accent" onClick={() => setGstins([...gstins, ""])}>
            + Add another GSTIN (other state)
          </button>
        </div>
        <Field label="Trade name (optional)" htmlFor="c-trade">
          <Input id="c-trade" value={form.trade_name} onChange={(e) => setForm({ ...form, trade_name: e.target.value })} />
        </Field>
        <Field label="GST filing" htmlFor="c-freq">
          <Select id="c-freq" value={form.filing_frequency} onChange={(e) => setForm({ ...form, filing_frequency: e.target.value })}>
            <option value="monthly">Monthly (GSTR-3B by the 20th)</option>
            <option value="quarterly">Quarterly (QRMP)</option>
          </Select>
        </Field>
        <Field label="Contact person" htmlFor="c-contact">
          <Input id="c-contact" value={form.contact_name} onChange={(e) => setForm({ ...form, contact_name: e.target.value })} />
        </Field>
        <Field label="Contact email" htmlFor="c-email">
          <Input id="c-email" type="email" value={form.contact_email} onChange={(e) => setForm({ ...form, contact_email: e.target.value })} />
        </Field>
        <Field label="Phone" htmlFor="c-phone">
          <Input id="c-phone" value={form.contact_phone} onChange={(e) => setForm({ ...form, contact_phone: e.target.value })} />
        </Field>
        <Field label="Language for messages" htmlFor="c-lang">
          <Select id="c-lang" value={form.language} onChange={(e) => setForm({ ...form, language: e.target.value })}>
            <option value="en">English</option>
            <option value="hi">Hindi</option>
            <option value="te">Telugu</option>
          </Select>
        </Field>
        <div className="flex justify-end gap-2 sm:col-span-2">
          <Button type="button" variant="ghost" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button type="submit" variant="primary" disabled={!valid} loading={m.isPending}>
            Add client
          </Button>
        </div>
      </form>
    </Dialog>
  );
}
