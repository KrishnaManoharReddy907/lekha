"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { FileJson } from "lucide-react";
import { useRef, useState } from "react";
import { toast } from "sonner";
import { api, errorMessage } from "@/lib/api";
import type { Client } from "@/lib/types";
import { Dialog } from "./dialog";
import { Button, Field, Input, Select } from "./ui";

/** Upload GSTR-2B (portal JSON or Excel). Period/GSTIN are read from the file when present. */
export function Upload2B({ client, onDone }: { client: Client; onDone?: (period: string) => void }) {
  const qc = useQueryClient();
  const ref = useRef<HTMLInputElement>(null);
  const [need, setNeed] = useState<{ file: File; code: string } | null>(null);
  const [period, setPeriod] = useState("");
  const [gstin, setGstin] = useState(client.gstins[0]?.gstin ?? "");
  const m = useMutation({
    mutationFn: async ({ file, p, g }: { file: File; p?: string; g?: string }) => {
      const form = new FormData();
      form.append("file", file);
      if (p) form.append("period", p);
      if (g) form.append("gstin", g);
      return api<{ period: string; record_count: number }>(`/api/clients/${client.id}/gstr2b`, { form });
    },
    onSuccess: (r) => {
      toast.success(`GSTR-2B imported: ${r.record_count} records. Reconciling…`);
      setNeed(null);
      qc.invalidateQueries({ queryKey: ["periods"] });
      setTimeout(() => qc.invalidateQueries({ queryKey: ["recon"] }), 1500);
      onDone?.(r.period);
    },
    onError: (e: unknown, vars) => {
      const code = (e as { code?: string }).code;
      if (code === "need_period" || code === "need_gstin") setNeed({ file: vars.file, code });
      else toast.error(errorMessage(e));
    },
  });
  return (
    <>
      <Button variant="primary" icon={<FileJson className="size-4" />} onClick={() => ref.current?.click()} loading={m.isPending}>
        Import GSTR-2B
      </Button>
      <input
        ref={ref}
        type="file"
        accept=".json,.xlsx,application/json"
        className="hidden"
        onChange={(e) => {
          const f = e.target.files?.[0];
          if (f) m.mutate({ file: f });
          e.target.value = "";
        }}
      />
      <Dialog open={!!need} onOpenChange={(o) => !o && setNeed(null)} title="A little more information" description="This file doesn't say which month or GSTIN it is for.">
        <form
          className="flex flex-col gap-4"
          onSubmit={(e) => {
            e.preventDefault();
            if (need) m.mutate({ file: need.file, p: period.replace(/^(\d{4})-(\d{2})$/, "$2$1"), g: gstin });
          }}
        >
          <Field label="Return period" htmlFor="p2b"><Input id="p2b" type="month" required value={period} onChange={(e) => setPeriod(e.target.value)} /></Field>
          <Field label="GSTIN" htmlFor="g2b">
            <Select id="g2b" value={gstin} onChange={(e) => setGstin(e.target.value)}>
              {client.gstins.map((g) => <option key={g.id} value={g.gstin}>{g.gstin}</option>)}
            </Select>
          </Field>
          <Button type="submit" variant="primary" loading={m.isPending}>Import</Button>
        </form>
      </Dialog>
      <p className="sr-only">Download GSTR-2B from gst.gov.in → Returns → GSTR-2B → Download JSON or Excel.</p>
    </>
  );
}
