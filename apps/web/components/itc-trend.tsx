"use client";

import { useState } from "react";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { inr, inrShort } from "@/lib/format";

export interface TrendMonth {
  period: string;
  label: string;
  itc: string;
  matched: string;
  missing: string;
}

/**
 * Monthly input tax credit: matched in GSTR-2B vs still missing (part-to-whole → stacked bars).
 * Colours are validated for colour-blind separation; aqua is below 3:1 on white, so the chart always
 * carries a legend, a hover tooltip and a table view.
 */
export function ItcTrend({ months }: { months: TrendMonth[] }) {
  const [table, setTable] = useState(false);
  const data = months.map((m) => ({ label: m.label, matched: Number(m.matched), missing: Number(m.missing) }));
  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center gap-4 text-[12.5px] text-ink-2">
        <span className="inline-flex items-center gap-1.5"><span className="size-2.5 rounded-sm" style={{ background: "var(--series-1)" }} /> Matched in GSTR-2B</span>
        <span className="inline-flex items-center gap-1.5"><span className="size-2.5 rounded-sm" style={{ background: "var(--series-2)" }} /> Not yet in GSTR-2B</span>
        <button className="ml-auto text-accent" onClick={() => setTable((t) => !t)} aria-pressed={table}>{table ? "View as chart" : "View as table"}</button>
      </div>
      {table ? (
        <div className="max-h-64 overflow-auto rounded-md border border-rule">
          <table className="w-full text-[12.5px]">
            <thead><tr className="bg-sunk text-left text-ink-2"><th className="px-2 py-1.5">Month</th><th className="px-2 py-1.5 text-right">Matched</th><th className="px-2 py-1.5 text-right">Missing</th></tr></thead>
            <tbody>{data.map((d) => <tr key={d.label} className="border-t border-rule"><td className="px-2 py-1">{d.label}</td><td className="num px-2 py-1 text-right">{inr(d.matched, { paise: false })}</td><td className="num px-2 py-1 text-right">{inr(d.missing, { paise: false })}</td></tr>)}</tbody>
          </table>
        </div>
      ) : (
        <div className="h-60 w-full" role="img" aria-label="Monthly ITC matched versus missing from GSTR-2B, last 12 months">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={data} margin={{ top: 4, right: 4, left: 0, bottom: 0 }} barCategoryGap="28%">
              <CartesianGrid vertical={false} stroke="var(--rule)" strokeDasharray="0" />
              <XAxis dataKey="label" tickLine={false} axisLine={{ stroke: "var(--rule-2)" }} tick={{ fill: "var(--ink-3)", fontSize: 11 }} />
              <YAxis tickLine={false} axisLine={false} width={52} tick={{ fill: "var(--ink-3)", fontSize: 11 }} tickFormatter={(v: number) => inrShort(v)} />
              <Tooltip
                cursor={{ fill: "var(--sunk)" }}
                contentStyle={{ background: "var(--sheet)", border: "1px solid var(--rule)", borderRadius: 8, fontSize: 12, color: "var(--ink)" }}
                labelStyle={{ color: "var(--ink)", fontWeight: 600 }}
                formatter={(v: number, name: string) => [inr(v, { paise: false }), name === "matched" ? "Matched in GSTR-2B" : "Not yet in GSTR-2B"]}
              />
              <Bar dataKey="matched" stackId="a" fill="var(--series-1)" stroke="var(--sheet)" strokeWidth={2} />
              <Bar dataKey="missing" stackId="a" fill="var(--series-2)" stroke="var(--sheet)" strokeWidth={2} radius={[4, 4, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      )}
    </div>
  );
}
