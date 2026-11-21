import type { Metadata } from "next";
import Image from "next/image";
import Link from "next/link";
import { DemoButton } from "@/components/demo-button";
import { Logo } from "@/components/logo";

export const metadata: Metadata = {
  title: "Lekha — protect every rupee of GST input tax credit",
  description:
    "Lekha reads purchase invoices with AI, checks them against GST rules, reconciles them with GSTR-2B, recommends IMS actions, tracks credit at risk and drafts notice replies — for CA firms and businesses in India.",
};

const STEPS = [
  ["Read", "Bills arrive as PDFs, scans or WhatsApp photos. Lekha reads every field, ties it to the exact spot on the page, and verifies the signed e-invoice QR."],
  ["Check", "GSTIN checksums, IGST vs CGST+SGST by place of supply, rates valid on the invoice date (including GST 2.0), arithmetic, duplicates, blocked credit under Sec 17(5)."],
  ["Match", "GSTR-2B is reconciled with optimal one-to-one matching. Every result explains itself — which field differs, by how much, and what to do."],
  ["Protect", "Rule 37, Rule 37A, Sec 16(4) and MSME 43B(h) deadlines in one ledger, in rupees. IMS recommendations, supplier follow-ups and Table 4 — approved by you."],
];

const NOVEL = [
  ["Signed-QR correction", "When AI misreads a GSTIN or total, the government-signed QR fixes it — but a genuine mismatch is kept as a tampering signal."],
  ["Recovery forecaster", "Will a missing invoice show up next month? A survival model learned from each supplier's filing history tells you whether to wait, chase or escalate."],
  ["Notice defence pack", "DRC-01C or ASMT-10 arrives: Lekha rebuilds each period, explains the difference with evidence and drafts the reply with a ZIP of proofs."],
  ["Fraud-ring detection", "Across all of a firm's clients: circular trading loops, shell suppliers, and one invoice template used by 'different' businesses."],
  ["Rules time-travel", "Every rate and deadline is versioned by date. Draft a rule change and see exactly which invoices and rupees it affects before publishing."],
  ["Monthly close", "A new GSTR-2B starts the month's close automatically. Lekha does the work; a person approves anything with legal effect."],
];

export default function Landing() {
  return (
    <div className="min-h-dvh bg-paper">
      <header className="mx-auto flex max-w-6xl items-center justify-between px-[max(16px,env(safe-area-inset-left))] py-5">
        <Logo />
        <nav className="flex items-center gap-2 text-[14px]">
          <Link href="/login" className="rounded-md px-3 py-2 text-ink-2 hover:text-ink">Sign in</Link>
          <Link href="/signup" className="rounded-md bg-accent px-3.5 py-2 font-medium text-accent-ink hover:brightness-110">Create workspace</Link>
        </nav>
      </header>

      <section className="mx-auto max-w-6xl px-[max(16px,env(safe-area-inset-left))] pt-10 pb-16">
        <div className="grid items-center gap-10 lg:grid-cols-[1fr_1.15fr]">
          <div>
            <p className="mb-3 text-[13px] font-semibold tracking-wide text-accent uppercase">For CA firms and Indian businesses</p>
            <h1 className="text-[clamp(34px,5vw,52px)] leading-[1.05] font-semibold">From invoice photo to protected tax credit.</h1>
            <p className="mt-5 max-w-xl text-[17px] leading-relaxed text-ink-2">
              Businesses lose input tax credit every month to typos, suppliers who never file, missed payment deadlines and blocked items nobody checked. Lekha reads every purchase bill, reconciles it with GSTR-2B and shows each rupee at risk — before the 20th.
            </p>
            <div className="mt-8 flex max-w-md flex-col gap-3">
              <DemoButton />
              <Link href="/signup" className="text-center text-[14px] font-medium text-accent">or create your own workspace →</Link>
            </div>
          </div>
          <div className="overflow-hidden rounded-xl border border-rule bg-sheet shadow-card">
            <Image src="/screens/reconciliation.png" alt="Lekha reconciliation: a missing invoice with an explanation and a recovery forecast" width={1440} height={900} priority className="h-auto w-full" />
          </div>
        </div>
      </section>

      <section className="border-y border-rule bg-sheet">
        <div className="mx-auto grid max-w-6xl gap-8 px-[max(16px,env(safe-area-inset-left))] py-14 md:grid-cols-4">
          {STEPS.map(([t, d], i) => (
            <div key={t}>
              <div className="num mb-3 text-[13px] text-accent">{String(i + 1).padStart(2, "0")}</div>
              <h3 className="text-[18px] font-semibold">{t}</h3>
              <p className="mt-2 text-[14px] leading-relaxed text-ink-2">{d}</p>
            </div>
          ))}
        </div>
      </section>

      <section className="mx-auto max-w-6xl px-[max(16px,env(safe-area-inset-left))] py-16">
        <h2 className="max-w-2xl text-[30px] font-semibold">What no spreadsheet — and few products — do</h2>
        <div className="mt-8 grid gap-x-10 gap-y-8 md:grid-cols-2 lg:grid-cols-3">
          {NOVEL.map(([t, d]) => (
            <div key={t} className="border-t border-rule pt-4">
              <h3 className="text-[16px] font-semibold">{t}</h3>
              <p className="mt-1.5 text-[14px] leading-relaxed text-ink-2">{d}</p>
            </div>
          ))}
        </div>
      </section>

      <section className="mx-auto grid max-w-6xl gap-6 px-[max(16px,env(safe-area-inset-left))] pb-16 lg:grid-cols-2">
        <figure className="overflow-hidden rounded-xl border border-rule bg-sheet">
          <Image src="/screens/review.png" alt="Review workstation: evidence highlighted on the bill" width={1440} height={900} className="h-auto w-full" />
          <figcaption className="border-t border-rule px-4 py-3 text-[13px] text-ink-2">Review workstation — click a field and see exactly where it was read on the bill.</figcaption>
        </figure>
        <figure className="overflow-hidden rounded-xl border border-rule bg-sheet">
          <Image src="/screens/fraud.png" alt="Fraud signals: a circular trading loop across three businesses" width={1440} height={1600} className="h-auto w-full" />
          <figcaption className="border-t border-rule px-4 py-3 text-[13px] text-ink-2">Fraud signals — a circular trading loop found across a firm's clients.</figcaption>
        </figure>
      </section>

      <footer className="border-t border-rule">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-4 px-[max(16px,env(safe-area-inset-left))] py-8 text-[13px] text-ink-3">
          <Logo />
          <p className="max-w-xl">Lekha recommends; people decide. Not tax advice. Rule tables carry effective dates and sources. Demo data is synthetic.</p>
        </div>
      </footer>
    </div>
  );
}
