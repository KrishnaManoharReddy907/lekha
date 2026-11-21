"use client";

import { useQuery } from "@tanstack/react-query";
import { ArrowRight, FileCheck2, Inbox, UploadCloud } from "lucide-react";
import Link from "next/link";
import { ButtonLink, Card, CardHeader, Empty, PageHeader, Stat } from "@/components/ui";
import { api } from "@/lib/api";
import { useClients } from "@/lib/clients";
import { inr, inrShort } from "@/lib/format";
import { useSession } from "@/lib/session";
import type { Invoice, Page } from "@/lib/types";

export default function Dashboard() {
  const { me } = useSession();
  const { current, clients, loading } = useClients();
  const inv = useQuery({
    queryKey: ["invoices", current?.id, "dash"],
    enabled: !!current,
    queryFn: () => api<Page<Invoice>>("/api/invoices", { params: { client_id: current?.id, limit: 500 } }),
  });
  const items = inv.data?.items ?? [];
  const review = items.filter((i) => i.status === "review");
  const approved = items.filter((i) => i.status === "approved");
  const itcApproved = approved.reduce((s, i) => s + Number(i.itc), 0);
  const itcReview = review.reduce((s, i) => s + Number(i.itc), 0);
  const blocked = items.reduce((s, i) => s + Number(i.blocked_amount || 0), 0);
  const flagged = items.filter((i) => i.flags?.length);

  if (!loading && clients.length === 0)
    return (
      <>
        <PageHeader title={`Welcome, ${me?.user.name.split(" ")[0]}`} sub="Three steps to your first protected tax credit." />
        <div className="grid gap-4 md:grid-cols-3">
          {[
            ["1", me?.firm?.kind === "business" ? "Add your business and GSTIN" : "Add a client and their GSTIN", "/clients?new=1", "Add client"],
            ["2", "Upload purchase invoices — PDFs or phone photos", "/inbox", "Open inbox"],
            ["3", "Review what Lekha read, then reconcile with GSTR-2B", "/review", "Review"],
          ].map(([n, t, href, cta]) => (
            <Card key={n}>
              <div className="num mb-3 grid size-8 place-items-center rounded-full bg-accent-soft font-semibold text-accent">{n}</div>
              <p className="mb-4 font-medium">{t}</p>
              <ButtonLink href={href} variant={n === "1" ? "primary" : "secondary"} size="sm">{cta}</ButtonLink>
            </Card>
          ))}
        </div>
      </>
    );

  return (
    <>
      <PageHeader
        eyebrow={me?.firm?.name}
        title={current?.name ?? "Dashboard"}
        sub="Tax credit status for this client."
        actions={<ButtonLink href="/inbox" variant="primary" icon={<UploadCloud className="size-4" />}>Upload invoices</ButtonLink>}
      />
      <div className="mb-6 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Stat label="Approved ITC" value={inrShort(itcApproved)} sub={`${approved.length} invoices ready to claim`} tone="ok" />
        <Stat label="Awaiting review" value={inrShort(itcReview)} sub={`${review.length} invoices`} tone={review.length ? "warn" : undefined} />
        <Stat label="Blocked credit 17(5)" value={inrShort(blocked)} sub="Cannot be claimed" tone={blocked ? "bad" : undefined} />
        <Stat label="Fraud & anomaly flags" value={flagged.length} sub="Invoices to double-check" tone={flagged.length ? "warn" : undefined} />
      </div>
      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader title="Review queue" sub="Lowest confidence first" action={review.length > 0 && <ButtonLink href="/review" size="sm" icon={<FileCheck2 className="size-3.5" />}>Start reviewing</ButtonLink>} />
          {review.length === 0 ? (
            <Empty icon={<Inbox className="size-7" />} title="All caught up">New uploads land here after Lekha reads them.</Empty>
          ) : (
            <ul className="divide-y divide-rule">
              {[...review].sort((a, b) => (a.confidence ?? 0) - (b.confidence ?? 0)).slice(0, 6).map((i) => (
                <li key={i.id}>
                  <Link href={`/review/${i.id}`} className="flex items-center justify-between gap-3 py-2.5 hover:text-accent">
                    <span className="min-w-0">
                      <span className="block truncate font-medium">{i.supplier_name || "Unknown supplier"}</span>
                      <span className="num text-[12px] text-ink-3">{i.number || "—"}</span>
                    </span>
                    <span className="num shrink-0 text-[13px]">{inr(i.itc)}</span>
                    <ArrowRight className="size-4 shrink-0 text-ink-3" />
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </Card>
        <Card>
          <CardHeader title="Flags to check" sub="Duplicates, re-photographed bills, unusual suppliers" />
          {flagged.length === 0 ? (
            <Empty title="No anomalies found">Lekha checks every invoice for duplicates and fake-invoice patterns.</Empty>
          ) : (
            <ul className="divide-y divide-rule">
              {flagged.slice(0, 6).map((i) => (
                <li key={i.id} className="py-2.5">
                  <Link href={`/review/${i.id}`} className="font-medium hover:text-accent">{i.supplier_name || i.number}</Link>
                  <p className="text-[12.5px] text-ink-3">{i.flags[0].message}</p>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
    </>
  );
}
