"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Camera, Copy, FileUp, Link2, RotateCw, UploadCloud } from "lucide-react";
import Link from "next/link";
import { useRef, useState } from "react";
import { toast } from "sonner";
import { Dialog } from "@/components/dialog";
import { Badge, Button, Card, ConfidenceMeter, Empty, PageHeader, StatusBadge, Table, Td, Th, cx } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { useClients } from "@/lib/clients";
import { inr, relTime } from "@/lib/format";
import { useSession } from "@/lib/session";
import type { DocumentRow, Page } from "@/lib/types";

export default function InboxPage() {
  const { current } = useClients();
  const { can } = useSession();
  const qc = useQueryClient();
  const fileRef = useRef<HTMLInputElement>(null);
  const camRef = useRef<HTMLInputElement>(null);
  const [drag, setDrag] = useState(false);
  const [linkOpen, setLinkOpen] = useState(false);

  const docs = useQuery({
    queryKey: ["documents", current?.id],
    enabled: !!current,
    queryFn: () => api<Page<DocumentRow>>("/api/documents", { params: { client_id: current?.id, limit: 100 } }),
    refetchInterval: (q) => (q.state.data?.items.some((d) => d.status === "queued" || d.status === "processing") ? 2000 : false),
  });

  const upload = useMutation({
    mutationFn: async ({ files, source }: { files: File[]; source: string }) => {
      const form = new FormData();
      files.forEach((f) => form.append("files", f));
      form.append("source", source);
      return api<{ documents: DocumentRow[]; errors: { filename: string; error: string }[] }>(`/api/clients/${current!.id}/documents`, { form });
    },
    onSuccess: (r) => {
      const dups = r.documents.filter((d) => d.status === "duplicate").length;
      const fresh = r.documents.length - dups;
      if (fresh) toast.success(`${fresh} document${fresh > 1 ? "s" : ""} uploaded — reading now`);
      if (dups) toast.info(`${dups} already uploaded before (skipped)`);
      r.errors.forEach((e) => toast.error(`${e.filename}: ${e.error}`));
      qc.invalidateQueries({ queryKey: ["documents"] });
    },
    onError: (e) => toast.error(errorMessage(e)),
  });

  function send(list: FileList | null, source = "upload") {
    if (!list || !list.length || !current) return;
    upload.mutate({ files: Array.from(list), source });
  }

  const retry = useMutation({
    mutationFn: (id: string) => api(`/api/documents/${id}/retry`, { method: "POST" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["documents"] }),
    onError: (e) => toast.error(errorMessage(e)),
  });

  if (!current)
    return (
      <>
        <PageHeader title="Inbox" />
        <Empty title="Add a client first" action={<Link className="text-accent" href="/clients?new=1">Add client</Link>}>
          Documents always belong to a client so Lekha knows whose GSTIN to expect.
        </Empty>
      </>
    );

  const items = docs.data?.items ?? [];
  const working = items.filter((d) => d.status === "queued" || d.status === "processing").length;

  return (
    <>
      <PageHeader
        title="Inbox"
        sub={<>Purchase bills for <span className="font-medium text-ink">{current.name}</span>. Lekha reads each one and sends it to review.</>}
        actions={can("write") && (
          <Button icon={<Link2 className="size-4" />} onClick={() => setLinkOpen(true)}>
            Client upload link
          </Button>
        )}
      />

      {can("write") && (
        <div
          onDragOver={(e) => {
            e.preventDefault();
            setDrag(true);
          }}
          onDragLeave={() => setDrag(false)}
          onDrop={(e) => {
            e.preventDefault();
            setDrag(false);
            send(e.dataTransfer.files);
          }}
          className={cx(
            "mb-6 flex flex-col items-center gap-3 rounded-xl border-2 border-dashed px-6 py-9 text-center transition",
            drag ? "border-accent bg-accent-soft" : "border-rule-2 bg-sheet",
          )}
        >
          <UploadCloud className="size-8 text-accent" aria-hidden />
          <div>
            <p className="font-medium">Drop invoices here</p>
            <p className="text-[13px] text-ink-3">PDF, JPG, PNG or HEIC · up to 50 at once · phone photos are fine</p>
          </div>
          <div className="flex flex-wrap justify-center gap-2">
            <Button variant="primary" icon={<FileUp className="size-4" />} onClick={() => fileRef.current?.click()} loading={upload.isPending}>
              Choose files
            </Button>
            <Button icon={<Camera className="size-4" />} onClick={() => camRef.current?.click()}>
              Take photo
            </Button>
          </div>
          <input ref={fileRef} type="file" multiple accept="application/pdf,image/*" className="hidden" onChange={(e) => { send(e.target.files); e.target.value = ""; }} />
          <input ref={camRef} type="file" accept="image/*" capture="environment" className="hidden" onChange={(e) => { send(e.target.files, "camera"); e.target.value = ""; }} />
        </div>
      )}

      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-[15px] font-semibold">Documents</h2>
        {working > 0 && <Badge tone="info">Reading {working}…</Badge>}
      </div>
      {items.length === 0 ? (
        <Empty title="No documents yet">Uploaded bills appear here while Lekha reads them, usually within a minute.</Empty>
      ) : (
        <Table>
          <thead>
            <tr>
              <Th>File</Th>
              <Th>Status</Th>
              <Th>Supplier</Th>
              <Th>Invoice</Th>
              <Th right>Total</Th>
              <Th>Confidence</Th>
              <Th>Uploaded</Th>
            </tr>
          </thead>
          <tbody>
            {items.map((d) => (
              <tr key={d.id} className="hover:bg-sunk/60">
                <Td className="max-w-[240px]">
                  <div className="truncate font-medium" title={d.filename}>{d.filename}</div>
                  <div className="text-[12px] text-ink-3">{d.source === "link" ? "via client link" : d.source === "camera" ? "camera" : `${Math.max(1, Math.round(d.size / 1024))} KB`}</div>
                </Td>
                <Td>
                  <div className="flex items-center gap-1.5">
                    <StatusBadge status={d.invoice && d.status === "extracted" ? d.invoice.status : d.status} />
                    {d.status === "failed" && can("write") && (
                      <button onClick={() => retry.mutate(d.id)} className="text-ink-3 hover:text-accent" title={d.error ?? "Retry"} aria-label="Retry">
                        <RotateCw className="size-3.5" />
                      </button>
                    )}
                  </div>
                </Td>
                <Td className="max-w-[220px] truncate">{d.invoice?.supplier_name ?? "—"}</Td>
                <Td mono>
                  {d.invoice ? (
                    <Link className="text-accent hover:underline" href={`/review/${d.invoice.id}`}>
                      {d.invoice.number || "(no number)"}
                    </Link>
                  ) : "—"}
                </Td>
                <Td right mono>{d.invoice ? inr(d.invoice.total) : "—"}</Td>
                <Td><ConfidenceMeter value={d.invoice?.confidence} /></Td>
                <Td className="text-ink-3">{relTime(d.created_at)}</Td>
              </tr>
            ))}
          </tbody>
        </Table>
      )}
      <UploadLinkDialog open={linkOpen} onOpenChange={setLinkOpen} clientId={current.id} clientName={current.name} />
    </>
  );
}

function UploadLinkDialog({ open, onOpenChange, clientId, clientName }: { open: boolean; onOpenChange: (o: boolean) => void; clientId: string; clientName: string }) {
  const [url, setUrl] = useState<string | null>(null);
  const create = useMutation({
    mutationFn: () => api<{ url: string }>(`/api/clients/${clientId}/upload-links`, { body: { days: 90 } }),
    onSuccess: (r) => setUrl(r.url),
    onError: (e) => toast.error(errorMessage(e)),
  });
  return (
    <Dialog open={open} onOpenChange={(o) => { onOpenChange(o); if (!o) setUrl(null); }} title="Client upload link" description={`${clientName} can send bills from a phone with this link. No login, no app.`}>
      {url ? (
        <Card className="bg-sunk">
          <p className="num break-all text-[13px]">{url}</p>
          <div className="mt-3 flex gap-2">
            <Button size="sm" variant="primary" icon={<Copy className="size-3.5" />} onClick={() => navigator.clipboard.writeText(url).then(() => toast.success("Link copied"))}>
              Copy link
            </Button>
          </div>
          <p className="mt-3 text-[12.5px] text-ink-3">Valid for 90 days. Share it on WhatsApp or email. You can revoke it from the client page.</p>
        </Card>
      ) : (
        <Button variant="primary" loading={create.isPending} onClick={() => create.mutate()}>
          Create link
        </Button>
      )}
    </Dialog>
  );
}
