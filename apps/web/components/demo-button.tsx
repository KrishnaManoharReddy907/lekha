"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { api, errorMessage } from "@/lib/api";
import { useAcceptMe } from "@/lib/session";
import type { Me } from "@/lib/types";
import { Button } from "./ui";

export function DemoButton() {
  const router = useRouter();
  const accept = useAcceptMe();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  return (
    <div className="flex flex-col gap-1.5">
      <Button
        type="button"
        className="w-full"
        loading={loading}
        onClick={async () => {
          setLoading(true);
          setError(null);
          try {
            const me = await api<Me>("/api/demo/start", { method: "POST" });
            accept(me);
            router.replace("/dashboard");
          } catch (err) {
            setError(errorMessage(err));
            setLoading(false);
          }
        }}
      >
        {loading ? "Building your private demo (about 30 s)…" : "Try the live demo — no sign-up"}
      </Button>
      <p className="text-center text-[12px] text-ink-3">A CA firm with 3 clients and 12 months of GST data, just for you.</p>
      {error && <p className="text-[12.5px] text-red">{error}</p>}
    </div>
  );
}

