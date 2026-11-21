"use client";

import { useQuery } from "@tanstack/react-query";
import { FileCheck2 } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect } from "react";
import { Empty, PageHeader, Spinner } from "@/components/ui";
import { api } from "@/lib/api";
import { useClients } from "@/lib/clients";

/** Opens the first invoice awaiting review (lowest confidence first). */
export default function ReviewIndex() {
  const { current } = useClients();
  const router = useRouter();
  const q = useQuery({
    queryKey: ["review-queue", current?.id],
    enabled: !!current,
    queryFn: () => api<{ id: string }[]>("/api/invoices/review-queue", { params: { client_id: current?.id } }),
  });
  useEffect(() => {
    if (q.data?.length) router.replace(`/review/${q.data[0].id}`);
  }, [q.data, router]);

  if (q.isLoading || q.data?.length)
    return (
      <div className="grid h-64 place-items-center">
        <Spinner className="size-6" />
      </div>
    );
  return (
    <>
      <PageHeader title="Review" />
      <Empty icon={<FileCheck2 className="size-8" />} title="Nothing to review" action={<Link href="/inbox" className="text-accent">Go to inbox</Link>}>
        Every invoice for {current?.name ?? "this client"} has been reviewed. New uploads appear here once Lekha has read them.
      </Empty>
    </>
  );
}
