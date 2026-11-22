"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { api, errorMessage, isTransient } from "@/lib/api";
import { useAcceptMe } from "@/lib/session";
import type { Me } from "@/lib/types";
import { Button } from "./ui";

const STAGES = [
  "Creating Rao & Associates (demo)…",
  "Adding 3 clients and 40 suppliers…",
  "Writing 12 months of purchase invoices…",
  "Generating GSTR-2B statements with real-world discrepancies…",
  "Reconciling every month…",
  "Scoring suppliers and forecasting recoveries…",
  "Looking for fraud patterns…",
  "Starting this month's close…",
];

/** Starts a private demo: instant sign-in, then the data is built in the background while we show progress. */
export function DemoButton() {
  const router = useRouter();
  const accept = useAcceptMe();
  const [phase, setPhase] = useState<"idle" | "building" | "error">("idle");
  const [error, setError] = useState<string | null>(null);
  const [elapsed, setElapsed] = useState(0);

  useEffect(() => {
    if (phase !== "building") return;
    const t = setInterval(() => setElapsed((e) => e + 1), 1000);
    return () => clearInterval(t);
  }, [phase]);

  async function start() {
    setPhase("building");
    setError(null);
    setElapsed(0);
    try {
      // The free server may be waking up: retry the start a few times on gateway errors.
      let me: Me | null = null;
      for (let attempt = 0; !me; attempt++) {
        try {
          me = await api<Me>("/api/demo/start", { method: "POST" });
        } catch (err) {
          if (!isTransient(err) || attempt >= 5) throw err;
          await new Promise((r) => setTimeout(r, 5000));
        }
      }
      accept(me);
      const started = Date.now();
      while (Date.now() - started < 480_000) {
        await new Promise((r) => setTimeout(r, 2000));
        let s: { status: string };
        try {
          s = await api<{ status: string }>("/api/demo/status");
        } catch (err) {
          if (isTransient(err)) continue; // a slow or restarting server: keep waiting
          throw err;
        }
        if (s.status === "ready") {
          router.replace("/dashboard");
          return;
        }
        if (s.status === "failed") throw new Error("Building the demo failed. Please try again.");
      }
      throw new Error("The demo is taking longer than expected. Refresh in a minute — it keeps building.");
    } catch (err) {
      setError(errorMessage(err));
      setPhase("error");
    }
  }

  const stage = STAGES[Math.min(STAGES.length - 1, Math.floor(elapsed / 5))];
  return (
    <div className="flex flex-col gap-1.5">
      <Button type="button" className="w-full" loading={phase === "building"} onClick={start}>
        {phase === "building" ? `Building your private demo · ${elapsed}s` : "Try the live demo — no sign-up"}
      </Button>
      <p className="text-center text-[12px] text-ink-3" aria-live="polite">
        {phase === "building" ? stage : "A CA firm with 3 clients and 12 months of GST data, just for you."}
      </p>
      {error && <p className="text-[12.5px] text-red">{error}</p>}
    </div>
  );
}
