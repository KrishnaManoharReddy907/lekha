import { BRAND } from "@/lib/brand";

export function Logo({ compact = false }: { compact?: boolean }) {
  return (
    <span className="inline-flex items-center gap-2">
      <svg width="26" height="26" viewBox="0 0 32 32" aria-hidden>
        <rect x="2" y="2" width="28" height="28" rx="7" fill="var(--accent)" />
        <path d="M10 8v16h12" stroke="var(--accent-ink)" strokeWidth="3" fill="none" strokeLinecap="round" strokeLinejoin="round" />
        <path d="M15 13h7M15 18h5" stroke="var(--accent-ink)" strokeWidth="2.2" strokeLinecap="round" opacity=".75" />
      </svg>
      {!compact && <span className="font-display text-[18px] font-semibold tracking-tight">{BRAND.name}</span>}
    </span>
  );
}
