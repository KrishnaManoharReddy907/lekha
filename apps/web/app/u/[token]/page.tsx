"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Camera, CheckCircle2, FileUp } from "lucide-react";
import { useParams } from "next/navigation";
import { useRef, useState } from "react";
import { Logo } from "@/components/logo";
import { Button, Spinner } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { relTime } from "@/lib/format";

interface Info {
  client_name: string;
  firm_name: string;
  recent: { filename: string; status: string; created_at: string }[];
}

/** Login-free page a client opens on their phone to send bills to their accountant. */
export default function PublicUpload() {
  const { token } = useParams<{ token: string }>();
  const qc = useQueryClient();
  const fileRef = useRef<HTMLInputElement>(null);
  const camRef = useRef<HTMLInputElement>(null);
  const [done, setDone] = useState<number | null>(null);
  const info = useQuery({ queryKey: ["public-upload", token], queryFn: () => api<Info>(`/api/public/upload/${token}`), retry: false });
  const up = useMutation({
    mutationFn: (files: File[]) => {
      const form = new FormData();
      files.forEach((f) => form.append("files", f));
      return api<{ uploaded: number; errors: { filename: string; error: string }[] }>(`/api/public/upload/${token}`, { form });
    },
    onSuccess: (r) => {
      setDone(r.uploaded);
      qc.invalidateQueries({ queryKey: ["public-upload", token] });
    },
  });
  const send = (l: FileList | null) => l && l.length && up.mutate(Array.from(l));

  return (
    <div className="mx-auto flex min-h-dvh max-w-md flex-col px-[max(16px,env(safe-area-inset-left))] py-8">
      <Logo />
      {info.isLoading ? (
        <div className="grid flex-1 place-items-center"><Spinner className="size-6" /></div>
      ) : info.isError ? (
        <div className="mt-16">
          <h1 className="text-[22px] font-semibold">Link not valid</h1>
          <p className="mt-2 text-ink-2">{errorMessage(info.error)}</p>
        </div>
      ) : (
        <>
          <h1 className="mt-10 text-[24px] font-semibold">Send bills to {info.data!.firm_name}</h1>
          <p className="mt-2 text-ink-2">
            For <span className="font-medium text-ink">{info.data!.client_name}</span>. Take a photo of each purchase bill or choose PDFs. Your accountant receives them instantly.
          </p>
          <div className="mt-8 flex flex-col gap-3">
            <Button variant="primary" className="h-12 text-[15px]" icon={<Camera className="size-5" />} onClick={() => camRef.current?.click()} loading={up.isPending}>
              Take a photo of a bill
            </Button>
            <Button className="h-12 text-[15px]" icon={<FileUp className="size-5" />} onClick={() => fileRef.current?.click()} disabled={up.isPending}>
              Choose files or PDFs
            </Button>
            <input ref={camRef} type="file" accept="image/*" capture="environment" className="hidden" onChange={(e) => { send(e.target.files); e.target.value = ""; }} />
            <input ref={fileRef} type="file" accept="application/pdf,image/*" multiple className="hidden" onChange={(e) => { send(e.target.files); e.target.value = ""; }} />
          </div>
          {done !== null && (
            <p className="mt-5 flex items-center gap-2 rounded-md bg-ok-soft px-3 py-2.5 text-ok">
              <CheckCircle2 className="size-5" /> {done} bill{done === 1 ? "" : "s"} sent. You can send more.
            </p>
          )}
          {up.isError && <p className="mt-5 rounded-md bg-red-soft px-3 py-2.5 text-red">{errorMessage(up.error)}</p>}
          {info.data!.recent.length > 0 && (
            <div className="mt-10">
              <h2 className="mb-2 text-[12px] font-semibold tracking-wider text-ink-3 uppercase">Sent recently</h2>
              <ul className="divide-y divide-rule rounded-lg border border-rule bg-sheet">
                {info.data!.recent.map((r, i) => (
                  <li key={i} className="flex items-center justify-between gap-3 px-3 py-2 text-[13px]">
                    <span className="truncate">{r.filename}</span>
                    <span className="shrink-0 text-ink-3">{relTime(r.created_at)}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
          <p className="mt-auto pt-10 text-center text-[12px] text-ink-3">Files go only to your accountant&apos;s private workspace.</p>
        </>
      )}
    </div>
  );
}
