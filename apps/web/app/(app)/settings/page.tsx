"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Copy, KeyRound, UserPlus } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { Dialog } from "@/components/dialog";
import { Badge, Button, Card, CardHeader, Field, Input, PageHeader, Select, Table, Td, Th } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { useClients } from "@/lib/clients";
import { date, relTime } from "@/lib/format";
import { useSession } from "@/lib/session";

interface FirmInfo {
  firm: { id: string; name: string; kind: string };
  role: string;
  members: { user_id: string; name: string; email: string; role: string; membership_id: string; client_ids: string[] }[];
  invites: { id: string; email: string; role: string; expires_at: string }[];
}
interface Key { id: string; name: string; prefix: string; role: string; created_at: string; last_used_at: string | null; revoked_at: string | null }

const ROLE_HELP: Record<string, string> = {
  owner: "Everything, including billing and deleting the workspace",
  partner: "Everything except removing owners",
  staff: "Upload, review, reconcile and file for all clients",
  client: "Upload and view their own business only",
  viewer: "Read-only access",
};

export default function SettingsPage() {
  const { can, me } = useSession();
  const qc = useQueryClient();
  const firm = useQuery({ queryKey: ["firm"], queryFn: () => api<FirmInfo>("/api/firm") });
  const keys = useQuery({ queryKey: ["api-keys"], enabled: can("admin"), queryFn: () => api<Key[]>("/api/api-keys") });
  const [inviteOpen, setInviteOpen] = useState(false);
  const [keyOpen, setKeyOpen] = useState(false);
  const [name, setName] = useState<string | null>(null);

  const rename = useMutation({
    mutationFn: () => api("/api/firm", { method: "PATCH", body: { name } }),
    onSuccess: () => {
      toast.success("Workspace renamed");
      qc.invalidateQueries({ queryKey: ["firm"] });
      qc.invalidateQueries({ queryKey: ["me"] });
      setName(null);
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const memberRole = useMutation({
    mutationFn: ({ id, role }: { id: string; role: string }) => api(`/api/members/${id}`, { method: "PATCH", body: { role } }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["firm"] }),
    onError: (e) => toast.error(errorMessage(e)),
  });
  const removeMember = useMutation({
    mutationFn: (id: string) => api(`/api/members/${id}`, { method: "DELETE" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["firm"] }),
    onError: (e) => toast.error(errorMessage(e)),
  });
  const revokeKey = useMutation({
    mutationFn: (id: string) => api(`/api/api-keys/${id}`, { method: "DELETE" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["api-keys"] }),
  });
  const revokeInvite = useMutation({
    mutationFn: (id: string) => api(`/api/invites/${id}`, { method: "DELETE" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["firm"] }),
  });

  return (
    <>
      <PageHeader title="Settings" sub="Workspace, team and API access." />
      <div className="grid gap-5">
        <Card>
          <CardHeader title="Workspace" />
          <div className="flex max-w-lg items-end gap-2">
            <div className="flex-1">
              <Field label="Name" htmlFor="ws-name">
                <Input id="ws-name" disabled={!can("admin")} value={name ?? firm.data?.firm.name ?? ""} onChange={(e) => setName(e.target.value)} />
              </Field>
            </div>
            {name !== null && name !== firm.data?.firm.name && (
              <Button variant="primary" loading={rename.isPending} onClick={() => rename.mutate()}>Save</Button>
            )}
          </div>
        </Card>

        <Card>
          <CardHeader
            title="Team"
            sub="Everyone with access to this workspace."
            action={can("admin") && <Button size="sm" variant="primary" icon={<UserPlus className="size-4" />} onClick={() => setInviteOpen(true)}>Invite</Button>}
          />
          <Table>
            <thead><tr><Th>Name</Th><Th>Email</Th><Th>Role</Th><Th /></tr></thead>
            <tbody>
              {firm.data?.members.map((m) => (
                <tr key={m.membership_id}>
                  <Td className="font-medium">{m.name}{m.user_id === me?.user.id && <Badge className="ml-2">You</Badge>}</Td>
                  <Td className="text-ink-2">{m.email}</Td>
                  <Td>
                    {can("admin") && m.user_id !== me?.user.id ? (
                      <Select aria-label={`Role of ${m.name}`} value={m.role} onChange={(e) => memberRole.mutate({ id: m.membership_id, role: e.target.value })} className="h-8 w-32">
                        {Object.keys(ROLE_HELP).map((r) => <option key={r} value={r}>{r}</option>)}
                      </Select>
                    ) : <span className="capitalize">{m.role}</span>}
                  </Td>
                  <Td right>
                    {can("admin") && m.user_id !== me?.user.id && (
                      <button className="text-[13px] text-red" onClick={() => removeMember.mutate(m.membership_id)}>Remove</button>
                    )}
                  </Td>
                </tr>
              ))}
              {firm.data?.invites.map((i) => (
                <tr key={i.id} className="text-ink-3">
                  <Td><Badge tone="info">Invited</Badge></Td>
                  <Td>{i.email}</Td>
                  <Td className="capitalize">{i.role}</Td>
                  <Td right>
                    <span className="mr-3 text-[12px]">expires {date(i.expires_at)}</span>
                    {can("admin") && <button className="text-[13px] text-red" onClick={() => revokeInvite.mutate(i.id)}>Revoke</button>}
                  </Td>
                </tr>
              ))}
            </tbody>
          </Table>
        </Card>

        {can("admin") && (
          <Card>
            <CardHeader
              title="API keys"
              sub="Connect your own software. Send as: Authorization: Bearer lk_…  · Docs at /docs on the API."
              action={<Button size="sm" icon={<KeyRound className="size-4" />} onClick={() => setKeyOpen(true)}>New key</Button>}
            />
            {keys.data?.length ? (
              <Table>
                <thead><tr><Th>Name</Th><Th>Key</Th><Th>Role</Th><Th>Last used</Th><Th /></tr></thead>
                <tbody>
                  {keys.data.map((k) => (
                    <tr key={k.id} className={k.revoked_at ? "opacity-50" : ""}>
                      <Td className="font-medium">{k.name}</Td>
                      <Td mono>{k.prefix}_••••</Td>
                      <Td className="capitalize">{k.role}</Td>
                      <Td className="text-ink-3">{k.last_used_at ? relTime(k.last_used_at) : "never"}</Td>
                      <Td right>{k.revoked_at ? <Badge>Revoked</Badge> : <button className="text-[13px] text-red" onClick={() => revokeKey.mutate(k.id)}>Revoke</button>}</Td>
                    </tr>
                  ))}
                </tbody>
              </Table>
            ) : <p className="text-[13px] text-ink-3">No API keys yet.</p>}
          </Card>
        )}
      </div>
      <InviteDialog open={inviteOpen} onOpenChange={setInviteOpen} />
      <KeyDialog open={keyOpen} onOpenChange={setKeyOpen} />
    </>
  );
}

function InviteDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
  const qc = useQueryClient();
  const { clients } = useClients();
  const [email, setEmail] = useState("");
  const [role, setRole] = useState("staff");
  const [clientIds, setClientIds] = useState<string[]>([]);
  const [url, setUrl] = useState<string | null>(null);
  const m = useMutation({
    mutationFn: () => api<{ url: string }>("/api/invites", { body: { email, role, client_ids: clientIds } }),
    onSuccess: (r) => { setUrl(r.url); qc.invalidateQueries({ queryKey: ["firm"] }); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  return (
    <Dialog open={open} onOpenChange={(o) => { onOpenChange(o); if (!o) { setUrl(null); setEmail(""); } }} title="Invite someone">
      {url ? (
        <div className="flex flex-col gap-3">
          <p className="text-[13px] text-ink-2">Send this link to {email}. It works once and expires in 7 days.</p>
          <p className="num break-all rounded-md bg-sunk p-3 text-[12.5px]">{url}</p>
          <Button variant="primary" icon={<Copy className="size-4" />} onClick={() => navigator.clipboard.writeText(url).then(() => toast.success("Copied"))}>Copy invite link</Button>
        </div>
      ) : (
        <form className="flex flex-col gap-4" onSubmit={(e) => { e.preventDefault(); m.mutate(); }}>
          <Field label="Email" htmlFor="inv-email"><Input id="inv-email" type="email" required value={email} onChange={(e) => setEmail(e.target.value)} /></Field>
          <Field label="Role" htmlFor="inv-role" hint={ROLE_HELP[role]}>
            <Select id="inv-role" value={role} onChange={(e) => setRole(e.target.value)}>
              {["partner", "staff", "client", "viewer"].map((r) => <option key={r} value={r}>{r}</option>)}
            </Select>
          </Field>
          {role === "client" && (
            <fieldset className="flex flex-col gap-1.5">
              <legend className="mb-1 text-[13px] font-medium text-ink-2">Which clients can they see?</legend>
              {clients.map((c) => (
                <label key={c.id} className="flex items-center gap-2 text-[13.5px]">
                  <input type="checkbox" checked={clientIds.includes(c.id)} onChange={(e) => setClientIds(e.target.checked ? [...clientIds, c.id] : clientIds.filter((x) => x !== c.id))} />
                  {c.name}
                </label>
              ))}
            </fieldset>
          )}
          <Button type="submit" variant="primary" loading={m.isPending}>Create invite</Button>
        </form>
      )}
    </Dialog>
  );
}

function KeyDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
  const qc = useQueryClient();
  const [name, setName] = useState("");
  const [role, setRole] = useState("staff");
  const [key, setKey] = useState<string | null>(null);
  const m = useMutation({
    mutationFn: () => api<{ key: string }>("/api/api-keys", { body: { name, role } }),
    onSuccess: (r) => { setKey(r.key); qc.invalidateQueries({ queryKey: ["api-keys"] }); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  return (
    <Dialog open={open} onOpenChange={(o) => { onOpenChange(o); if (!o) { setKey(null); setName(""); } }} title="New API key">
      {key ? (
        <div className="flex flex-col gap-3">
          <p className="text-[13px] text-amber">Copy this key now. It is shown only once.</p>
          <p className="num break-all rounded-md bg-sunk p-3 text-[12.5px]">{key}</p>
          <Button variant="primary" icon={<Copy className="size-4" />} onClick={() => navigator.clipboard.writeText(key).then(() => toast.success("Copied"))}>Copy key</Button>
        </div>
      ) : (
        <form className="flex flex-col gap-4" onSubmit={(e) => { e.preventDefault(); m.mutate(); }}>
          <Field label="Name" htmlFor="key-name" hint="e.g. Tally sync, billing system"><Input id="key-name" required value={name} onChange={(e) => setName(e.target.value)} /></Field>
          <Field label="Access" htmlFor="key-role">
            <Select id="key-role" value={role} onChange={(e) => setRole(e.target.value)}>
              <option value="staff">Read and write</option>
              <option value="viewer">Read only</option>
            </Select>
          </Field>
          <Button type="submit" variant="primary" loading={m.isPending}>Create key</Button>
        </form>
      )}
    </Dialog>
  );
}
