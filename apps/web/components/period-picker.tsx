"use client";

import { period as fmtPeriod } from "@/lib/format";
import type { PeriodInfo } from "@/lib/gst";
import { Select } from "./ui";

export function PeriodPicker({ periods, value, onChange }: { periods: PeriodInfo[]; value: string | null; onChange: (p: string) => void }) {
  return (
    <Select aria-label="Return period" value={value ?? ""} onChange={(e) => onChange(e.target.value)} className="w-52">
      {periods.map((p) => (
        <option key={p.period} value={p.period}>
          {fmtPeriod(p.period)} {p.has_2b ? "· 2B imported" : "· no 2B yet"}
        </option>
      ))}
    </Select>
  );
}
