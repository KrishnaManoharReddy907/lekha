"use client";

import { Badge } from "./ui";

export interface SupplierRow {
  id: string;
  gstin: string;
  legal_name: string | null;
  trade_name: string | null;
  msme_category: string | null;
  score: number | null;
  score_detail: { grade?: string; on_time_rate?: number; mismatch_rate?: number; missing_rate?: number; avg_lag_months?: number };
  invoices: number;
  itc: string;
  at_risk: string;
  first_seen: string | null;
}

export function Grade({ score, grade }: { score: number | null; grade?: string }) {
  if (score === null) return <Badge>New</Badge>;
  const tone = grade === "A" ? "ok" : grade === "B" ? "accent" : grade === "C" ? "warn" : "bad";
  return <Badge tone={tone} title={`Compliance score ${score}/100`}>{grade} · {Math.round(score)}</Badge>;
}

