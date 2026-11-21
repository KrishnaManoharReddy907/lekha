import Link from "next/link";
import type { ReactNode } from "react";
import { BRAND } from "@/lib/brand";
import { Logo } from "./logo";

export function AuthShell({ title, sub, children, footer }: { title: string; sub?: ReactNode; children: ReactNode; footer?: ReactNode }) {
  return (
    <div className="grid min-h-dvh lg:grid-cols-[1fr_1.1fr]">
      <div className="flex flex-col px-[max(16px,env(safe-area-inset-left))] py-8 sm:px-12">
        <Link href="/" className="flex items-center gap-2">
          <Logo />
        </Link>
        <div className="mx-auto flex w-full max-w-sm flex-1 flex-col justify-center py-10">
          <h1 className="text-[26px] font-semibold">{title}</h1>
          {sub ? <p className="mt-1.5 text-ink-3">{sub}</p> : null}
          <div className="mt-7">{children}</div>
          {footer ? <div className="mt-6 text-[13.5px] text-ink-3">{footer}</div> : null}
        </div>
      </div>
      <aside className="relative hidden overflow-hidden border-l border-rule bg-sunk lg:block">
        <LedgerArt />
        <div className="absolute inset-x-12 bottom-12">
          <p className="font-display text-[28px] leading-tight font-semibold text-ink">{BRAND.tagline}.</p>
          <p className="mt-3 max-w-md text-ink-2">
            Read invoices with AI, verify them against GST rules, match them with GSTR-2B, and see every rupee of input tax credit at risk before the 20th.
          </p>
        </div>
      </aside>
    </div>
  );
}

/** A quiet ledger-sheet illustration: ruled lines with matched / mismatched entries. */
function LedgerArt() {
  const rows = [
    ["SBT/25-26/0412", "18,000.00", "ok"],
    ["HH-0098", "4,320.00", "ok"],
    ["AP7731", "9,000.00", "warn"],
    ["RG/221", "5,400.00", "bad"],
    ["KT-1102", "1,800.00", "warn"],
    ["VSF-55", "2,700.00", "ok"],
    ["INV/25-26/0042", "12,600.00", "ok"],
  ];
  return (
    <div className="absolute inset-x-12 top-16 rounded-lg border border-rule bg-sheet p-5 shadow-card" aria-hidden>
      <div className="mb-3 flex justify-between text-[11px] font-semibold tracking-wider text-ink-3 uppercase">
        <span>Invoice</span>
        <span>ITC (₹)</span>
        <span>GSTR-2B</span>
      </div>
      {rows.map(([n, v, s]) => (
        <div key={n} className="flex items-center justify-between border-t border-rule py-2.5 text-[13px]">
          <span className="num text-ink-2">{n}</span>
          <span className="num">{v}</span>
          <span className={s === "ok" ? "text-ok" : s === "warn" ? "text-amber" : "text-red"}>{s === "ok" ? "Matched" : s === "warn" ? "At risk" : "Missing"}</span>
        </div>
      ))}
    </div>
  );
}
