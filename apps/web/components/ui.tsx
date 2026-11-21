"use client";

import clsx from "clsx";
import { Loader2 } from "lucide-react";
import Link from "next/link";
import { forwardRef, type ButtonHTMLAttributes, type InputHTMLAttributes, type ReactNode, type SelectHTMLAttributes, type TextareaHTMLAttributes } from "react";

export const cx = clsx;

type BtnVariant = "primary" | "secondary" | "ghost" | "danger";
const BTN: Record<BtnVariant, string> = {
  primary: "bg-accent text-accent-ink hover:brightness-110 border border-transparent",
  secondary: "bg-sheet text-ink border border-rule-2 hover:bg-sunk",
  ghost: "bg-transparent text-ink-2 hover:bg-sunk hover:text-ink border border-transparent",
  danger: "bg-red text-white hover:brightness-110 border border-transparent",
};

interface BtnProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: BtnVariant;
  size?: "sm" | "md";
  loading?: boolean;
  icon?: ReactNode;
}

export const Button = forwardRef<HTMLButtonElement, BtnProps>(function Button(
  { variant = "secondary", size = "md", loading, icon, className, children, disabled, ...rest },
  ref,
) {
  return (
    <button
      ref={ref}
      disabled={disabled || loading}
      className={cx(
        "inline-flex items-center justify-center gap-1.5 rounded-md font-medium whitespace-nowrap transition disabled:opacity-50 disabled:cursor-not-allowed",
        size === "sm" ? "h-8 px-2.5 text-[13px]" : "h-9 px-3.5 text-sm",
        BTN[variant],
        className,
      )}
      {...rest}
    >
      {loading ? <Loader2 className="size-4 animate-spin" aria-hidden /> : icon}
      {children}
    </button>
  );
});

export function ButtonLink({ href, variant = "secondary", size = "md", icon, children, className }: { href: string; variant?: BtnVariant; size?: "sm" | "md"; icon?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <Link
      href={href}
      className={cx(
        "inline-flex items-center justify-center gap-1.5 rounded-md font-medium whitespace-nowrap transition",
        size === "sm" ? "h-8 px-2.5 text-[13px]" : "h-9 px-3.5 text-sm",
        BTN[variant],
        className,
      )}
    >
      {icon}
      {children}
    </Link>
  );
}

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement> & { invalid?: boolean }>(function Input(
  { className, invalid, ...rest },
  ref,
) {
  return (
    <input
      ref={ref}
      className={cx(
        "h-9 w-full rounded-md border bg-sheet px-3 text-sm text-ink placeholder:text-ink-3 outline-none transition focus:border-accent focus:ring-2 focus:ring-accent-soft",
        invalid ? "border-red" : "border-rule-2",
        className,
      )}
      {...rest}
    />
  );
});

export const Select = forwardRef<HTMLSelectElement, SelectHTMLAttributes<HTMLSelectElement>>(function Select({ className, children, ...rest }, ref) {
  return (
    <select ref={ref} className={cx("h-9 w-full rounded-md border border-rule-2 bg-sheet px-2.5 text-sm text-ink outline-none focus:border-accent focus:ring-2 focus:ring-accent-soft", className)} {...rest}>
      {children}
    </select>
  );
});

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaHTMLAttributes<HTMLTextAreaElement>>(function Textarea({ className, ...rest }, ref) {
  return <textarea ref={ref} className={cx("w-full rounded-md border border-rule-2 bg-sheet px-3 py-2 text-sm text-ink outline-none focus:border-accent focus:ring-2 focus:ring-accent-soft", className)} {...rest} />;
});

export function Field({ label, hint, error, children, htmlFor }: { label: string; hint?: ReactNode; error?: string | null; children: ReactNode; htmlFor?: string }) {
  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={htmlFor} className="text-[13px] font-medium text-ink-2">
        {label}
      </label>
      {children}
      {error ? <p className="text-[12.5px] text-red">{error}</p> : hint ? <p className="text-[12.5px] text-ink-3">{hint}</p> : null}
    </div>
  );
}

export function Card({ children, className, pad = true }: { children: ReactNode; className?: string; pad?: boolean }) {
  return <div className={cx("rounded-lg border border-rule bg-sheet", pad && "p-5", className)}>{children}</div>;
}

export function CardHeader({ title, action, sub }: { title: ReactNode; action?: ReactNode; sub?: ReactNode }) {
  return (
    <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
      <div>
        <h3 className="text-[15px] font-semibold">{title}</h3>
        {sub ? <p className="mt-0.5 text-[13px] text-ink-3">{sub}</p> : null}
      </div>
      {action}
    </div>
  );
}

type Tone = "neutral" | "ok" | "warn" | "bad" | "info" | "accent";
const TONE: Record<Tone, string> = {
  neutral: "bg-sunk text-ink-2",
  ok: "bg-ok-soft text-ok",
  warn: "bg-amber-soft text-amber",
  bad: "bg-red-soft text-red",
  info: "bg-blue-soft text-blue",
  accent: "bg-accent-soft text-accent",
};

export function Badge({ tone = "neutral", children, className, title }: { tone?: Tone; children: ReactNode; className?: string; title?: string }) {
  return (
    <span title={title} className={cx("inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11.5px] font-semibold whitespace-nowrap", TONE[tone], className)}>
      {children}
    </span>
  );
}

export function Spinner({ className }: { className?: string }) {
  return <Loader2 className={cx("size-4 animate-spin text-ink-3", className)} aria-label="Loading" />;
}

export function Empty({ icon, title, children, action }: { icon?: ReactNode; title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-rule-2 px-6 py-12 text-center">
      {icon ? <div className="mb-1 text-ink-3">{icon}</div> : null}
      <p className="font-medium">{title}</p>
      {children ? <div className="max-w-md text-[13px] text-ink-3">{children}</div> : null}
      {action ? <div className="mt-2">{action}</div> : null}
    </div>
  );
}

export function PageHeader({ title, sub, actions, eyebrow }: { title: ReactNode; sub?: ReactNode; actions?: ReactNode; eyebrow?: ReactNode }) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
      <div className="min-w-0">
        {eyebrow ? <div className="mb-1 text-[12px] font-semibold tracking-wide text-accent uppercase">{eyebrow}</div> : null}
        <h1 className="text-[22px] font-semibold">{title}</h1>
        {sub ? <p className="mt-1 text-[13.5px] text-ink-3">{sub}</p> : null}
      </div>
      {actions ? <div className="flex flex-wrap items-center gap-2">{actions}</div> : null}
    </div>
  );
}

export function Stat({ label, value, sub, tone }: { label: string; value: ReactNode; sub?: ReactNode; tone?: "ok" | "warn" | "bad" }) {
  return (
    <div className="flex flex-col gap-1 rounded-lg border border-rule bg-sheet p-4">
      <span className="text-[12px] font-medium tracking-wide text-ink-3 uppercase">{label}</span>
      <span className={cx("num text-[22px] font-semibold", tone === "ok" && "text-ok", tone === "warn" && "text-amber", tone === "bad" && "text-red")}>{value}</span>
      {sub ? <span className="text-[12.5px] text-ink-3">{sub}</span> : null}
    </div>
  );
}

export function Table({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div className={cx("overflow-x-auto rounded-lg border border-rule bg-sheet", className)}>
      <table className="w-full border-collapse text-[13.5px]">{children}</table>
    </div>
  );
}

export function Th({ children, right, className }: { children?: ReactNode; right?: boolean; className?: string }) {
  return <th className={cx("border-b border-rule bg-sunk px-3 py-2 text-left text-[12px] font-semibold text-ink-2", right && "text-right", className)}>{children}</th>;
}

export function Td({ children, right, mono, className, colSpan }: { children?: ReactNode; right?: boolean; mono?: boolean; className?: string; colSpan?: number }) {
  return (
    <td colSpan={colSpan} className={cx("border-b border-rule px-3 py-2 align-middle", right && "text-right", mono && "num", className)}>
      {children}
    </td>
  );
}

export function ConfidenceMeter({ value }: { value: number | null | undefined }) {
  if (value === null || value === undefined) return <span className="text-ink-3">—</span>;
  const tone = value >= 0.9 ? "bg-ok" : value >= 0.7 ? "bg-amber" : "bg-red";
  return (
    <span className="inline-flex items-center gap-1.5" title={`Confidence ${(value * 100).toFixed(0)}%`}>
      <span className="h-1.5 w-12 overflow-hidden rounded-full bg-sunk">
        <span className={cx("block h-full rounded-full", tone)} style={{ width: `${Math.max(4, value * 100)}%` }} />
      </span>
      <span className="num text-[12px] text-ink-2">{(value * 100).toFixed(0)}%</span>
    </span>
  );
}

export function StatusBadge({ status }: { status: string }) {
  const map: Record<string, [Tone, string]> = {
    review: ["warn", "Needs review"],
    approved: ["ok", "Approved"],
    rejected: ["bad", "Rejected"],
    duplicate: ["neutral", "Duplicate"],
    queued: ["neutral", "Queued"],
    processing: ["info", "Reading…"],
    extracted: ["ok", "Read"],
    failed: ["bad", "Failed"],
    matched: ["ok", "Matched"],
    mismatch: ["warn", "Mismatch"],
    missing_in_2b: ["bad", "Missing in 2B"],
    missing_in_books: ["info", "Missing in books"],
    open: ["warn", "Open"],
    resolved: ["ok", "Resolved"],
  };
  const [tone, label] = map[status] ?? ["neutral", status];
  return <Badge tone={tone}>{label}</Badge>;
}
