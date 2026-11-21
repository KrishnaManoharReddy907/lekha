"use client";

import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api } from "./api";

export interface PeriodInfo {
  period: string;
  invoices: number;
  has_2b: boolean;
  import_id: string | null;
  generated_on: string | null;
  records: number;
}

/** Periods with invoices or a GSTR-2B for a client; selects the latest one with a 2B by default. */
export function usePeriods(clientId: string | undefined) {
  const q = useQuery({
    queryKey: ["periods", clientId],
    enabled: !!clientId,
    queryFn: () => api<PeriodInfo[]>(`/api/clients/${clientId}/periods`),
  });
  const [period, setPeriod] = useState<string | null>(null);
  useEffect(() => {
    if (!q.data?.length) return;
    if (period && q.data.some((p) => p.period === period)) return;
    setPeriod((q.data.find((p) => p.has_2b) ?? q.data[0]).period);
  }, [q.data, period]);
  useEffect(() => setPeriod(null), [clientId]);
  return { periods: q.data ?? [], period, setPeriod, loading: q.isLoading, refetch: q.refetch };
}

export interface Explanation {
  lines: { feature: string; ok: boolean; weight: number; value: number; text: string }[];
  summary: string;
}

export interface MatchRow {
  id: string;
  kind: string;
  status: "matched" | "mismatch" | "missing_in_2b" | "missing_in_books";
  mismatch_types: string[];
  score: number;
  itc_books: string;
  itc_2b: string;
  explanation: Explanation;
  invoice: { id: string; number: string; date: string | null; supplier_name: string | null; supplier_gstin: string | null; taxable_value: string; itc: string; status: string } | null;
  record: { id: string; number: string; date: string | null; supplier_name: string | null; supplier_gstin: string; taxable_value: string; itc: string; itc_available: boolean; reason: string | null; doc_type: string } | null;
  ims: { recommendation: string; decided_action: string | null; reason_text: string } | null;
  forecast: { p_next: number; p_by_deadline: number; recommendation: string; explanation: string } | null;
}

export const MISMATCH_LABEL: Record<string, string> = {
  NUMBER_DIFFERENT: "Number differs",
  DATE_DIFFERENT: "Date differs",
  TAXABLE_VALUE_DIFFERENT: "Taxable value differs",
  TAX_AMOUNT_DIFFERENT: "Tax differs",
  TAX_HEAD_DIFFERENT: "IGST vs CGST/SGST",
  DOC_TYPE_DIFFERENT: "Doc type differs",
  GSTIN_DIFFERENT: "GSTIN typo",
  OTHER_STATE_REGISTRATION: "Other-state GSTIN",
  ITC_NOT_AVAILABLE_IN_2B: "ITC not available",
  SPLIT: "Split entries",
  NOT_IN_2B: "Not in 2B",
  NOT_IN_BOOKS: "Not in books",
};

export const RISK_LABEL: Record<string, string> = {
  NOT_IN_2B: "Not in GSTR-2B",
  VALUE_MISMATCH: "Supplier under-reported",
  WRONG_TAX_HEAD: "Wrong tax head",
  R37_PAYMENT: "180-day payment (Rule 37)",
  R37A_SUPPLIER_3B: "Supplier hasn't paid tax (Rule 37A)",
  MSME_43BH: "MSME 45-day payment (43B(h))",
  S16_4_DEADLINE: "Claim deadline (Sec 16(4))",
  S17_5_BLOCKED: "Blocked credit (Sec 17(5))",
  NOT_RECEIVED: "Goods not received",
};
