"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";
import { Badge, Button, Card, CardHeader, Input, PageHeader, Spinner, Table, Td, Th } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { STATE_NAMES, date, relTime } from "@/lib/format";
import { gstinProblem, normaliseGstin } from "@/lib/gstin";
import { useSession } from "@/lib/session";
import type { Client } from "@/lib/types";

interface Link_ { id: string; label: string | null; expires_at: string | null; revoked_at: string | null; use_count: number; created_at: string }

export default function ClientDetail() {
  const { id } = useParams<{ id: string }>();
  const qc = useQueryClient();
  const { can } = useSession();
  const client = useQuery({ queryKey: ["client", id], queryFn: () => api<Client>(`/api/clients/${id}`) });
  const links = useQuery({ queryKey: ["upload-links", id], queryFn: () => api<Link_[]>(`/api/clients/${id}/upload-links`) });
  const [gstin, setGstin] = useState("");
  const problem = gstinProblem(gstin);

  const addGstin = useMutation({
    mutationFn: () => api(`/api/clients/${id}/gstins`, { body: { gstin: normaliseGstin(gstin) } }),
    onSuccess: () => {
      setGstin("");
      qc.invalidateQueries({ queryKey: ["client", id] });
      qc.invalidateQueries({ queryKey: ["clients"] });
      toast.success("GSTIN added");
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const delGstin = useMutation({
    mutationFn: (gid: string) => api(`/api/clients/${id}/gstins/${gid}`, { method: "DELETE" }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["client", id] }); qc.invalidateQueries({ queryKey: ["clients"] }); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const revoke = useMutation({
    mutationFn: (lid: string) => api(`/api/links/${lid}`, { method: "DELETE" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["upload-links", id] }),
  });

  if (!client.data) return <div className="grid h-64 place-items-center"><Spinner className="size-6" /></div>;
  const c = client.data;
  return (
    <>
      <Link href="/clients" className="mb-3 inline-flex items-center gap-1 text-[13px] text-ink-3 hover:text-ink"><ArrowLeft className="size-3.5" /> Clients</Link>
      <PageHeader title={c.name} sub={[c.trade_name, c.contact_name, c.contact_email, c.contact_phone].filter(Boolean).join(" · ") || undefined} />
      <div className="grid gap-5 lg:grid-cols-2">
        <Card>
          <CardHeader title="GSTINs" sub="Invoices billed to these GSTINs belong to this client." />
          <ul className="mb-4 divide-y divide-rule">
            {c.gstins.map((g) => (
              <li key={g.id} className="flex items-center justify-between gap-3 py-2">
                <span>
                  <span className="num font-medium">{g.gstin}</span>
                  {g.is_primary && <Badge className="ml-2" tone="accent">Primary</Badge>}
                  <span className="block text-[12px] text-ink-3">{STATE_NAMES[g.state_code]}</span>
                </span>
                {can("admin") && c.gstins.length > 1 && <button className="text-[13px] text-red" onClick={() => delGstin.mutate(g.id)}>Remove</button>}
              </li>
            ))}
          </ul>
          {can("write") && (
            <form className="flex items-start gap-2" onSubmit={(e) => { e.preventDefault(); if (!problem && gstin) addGstin.mutate(); }}>
              <div className="flex-1">
                <Input aria-label="New GSTIN" value={gstin} invalid={!!problem} onChange={(e) => setGstin(e.target.value.toUpperCase())} placeholder="Add GSTIN for another state" className="num" />
                {problem && <p className="mt-1 text-[12px] text-red">{problem}</p>}
              </div>
              <Button type="submit" disabled={!gstin || !!problem} loading={addGstin.isPending}>Add</Button>
            </form>
          )}
        </Card>
        <Card>
          <CardHeader title="Upload links" sub="Login-free links the client uses to send bills. Create new ones from the Inbox." />
          {links.data?.length ? (
            <Table>
              <thead><tr><Th>Created</Th><Th>Uploads</Th><Th>Expires</Th><Th /></tr></thead>
              <tbody>
                {links.data.map((l) => (
                  <tr key={l.id} className={l.revoked_at ? "opacity-50" : ""}>
                    <Td>{relTime(l.created_at)}</Td>
                    <Td mono>{l.use_count}</Td>
                    <Td>{date(l.expires_at)}</Td>
                    <Td right>{l.revoked_at ? <Badge>Revoked</Badge> : can("write") && <button className="text-[13px] text-red" onClick={() => revoke.mutate(l.id)}>Revoke</button>}</Td>
                  </tr>
                ))}
              </tbody>
            </Table>
          ) : <p className="text-[13px] text-ink-3">No links yet.</p>}
        </Card>
      </div>
    </>
  );
}
