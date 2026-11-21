"use client";

import { useQueryClient } from "@tanstack/react-query";
import {
  Banknote,
  Building2,
  GitCompareArrows,
  ListChecks,
  Network,
  ShieldAlert,
  Table2,
  Truck,
  ChevronsUpDown,
  FileCheck2,
  FileText,
  Inbox,
  LayoutDashboard,
  LogOut,
  Menu,
  Settings,
  X,
  type LucideIcon,
} from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";
import { api } from "@/lib/api";
import { ClientProvider, useClients } from "@/lib/clients";
import { SessionProvider, useSession } from "@/lib/session";
import { Logo } from "./logo";
import { Spinner, cx } from "./ui";


export interface NavItem {
  href: string;
  label: string;
  icon: LucideIcon;
  group: "work" | "gst" | "insight" | "admin";
}

/** Navigation registry. Later modules append their entries here. */
export const NAV: NavItem[] = [
  { href: "/dashboard", label: "Dashboard", icon: LayoutDashboard, group: "work" },
  { href: "/inbox", label: "Inbox", icon: Inbox, group: "work" },
  { href: "/review", label: "Review", icon: FileCheck2, group: "work" },
  { href: "/invoices", label: "Purchase register", icon: FileText, group: "work" },
  { href: "/reconciliation", label: "Reconciliation", icon: GitCompareArrows, group: "gst" },
  { href: "/ims", label: "IMS actions", icon: ListChecks, group: "gst" },
  { href: "/risk", label: "Credit at risk", icon: ShieldAlert, group: "gst" },
  { href: "/table4", label: "GSTR-3B Table 4", icon: Table2, group: "gst" },
  { href: "/payables", label: "Payables", icon: Banknote, group: "gst" },
  { href: "/suppliers", label: "Suppliers", icon: Truck, group: "insight" },
  { href: "/intelligence", label: "Fraud signals", icon: Network, group: "insight" },
  { href: "/clients", label: "Clients", icon: Building2, group: "admin" },
  { href: "/settings", label: "Settings", icon: Settings, group: "admin" },
];

const GROUPS: Record<NavItem["group"], string> = { work: "Daily work", gst: "GST", insight: "Intelligence", admin: "Workspace" };

export function AppShell({ children }: { children: ReactNode }) {
  return (
    <SessionProvider>
      <Guard>
        <ClientProvider>
          <Frame>{children}</Frame>
        </ClientProvider>
      </Guard>
    </SessionProvider>
  );
}

function Guard({ children }: { children: ReactNode }) {
  const { me, loading, error } = useSession();
  const router = useRouter();
  const path = usePathname();
  useEffect(() => {
    if (!loading && (error || !me)) router.replace(`/login?next=${encodeURIComponent(path)}`);
    else if (me && !me.firm) router.replace("/onboarding");
  }, [loading, error, me, router, path]);
  if (loading || !me || !me.firm)
    return (
      <div className="grid min-h-dvh place-items-center">
        <Spinner className="size-6" />
      </div>
    );
  return <>{children}</>;
}

function Frame({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const path = usePathname();
  useEffect(() => setOpen(false), [path]);
  return (
    <div className="min-h-dvh lg:grid lg:grid-cols-[248px_1fr]">
      <aside
        className={cx(
          "fixed inset-y-0 left-0 z-40 flex w-[248px] flex-col border-r border-rule bg-sheet pt-[env(safe-area-inset-top)] transition-transform lg:sticky lg:top-0 lg:h-dvh lg:translate-x-0",
          open ? "translate-x-0" : "-translate-x-full",
        )}
      >
        <div className="flex h-14 items-center justify-between px-4">
          <Link href="/dashboard">
            <Logo />
          </Link>
          <button className="rounded p-1 text-ink-3 lg:hidden" onClick={() => setOpen(false)} aria-label="Close menu">
            <X className="size-5" />
          </button>
        </div>
        <ClientSwitcher />
        <nav className="flex-1 overflow-y-auto px-2 pb-4" aria-label="Main">
          {(Object.keys(GROUPS) as NavItem["group"][]).map((g) => {
            const items = NAV.filter((n) => n.group === g);
            if (!items.length) return null;
            return (
              <div key={g} className="mt-4">
                <div className="px-2 pb-1 text-[11px] font-semibold tracking-wider text-ink-3 uppercase">{GROUPS[g]}</div>
                {items.map((n) => {
                  const active = path === n.href || path.startsWith(`${n.href}/`);
                  return (
                    <Link
                      key={n.href}
                      href={n.href}
                      className={cx(
                        "flex items-center gap-2.5 rounded-md px-2 py-1.5 text-[13.5px] transition",
                        active ? "bg-accent-soft font-medium text-accent" : "text-ink-2 hover:bg-sunk hover:text-ink",
                      )}
                    >
                      <n.icon className="size-4 shrink-0" aria-hidden />
                      {n.label}
                    </Link>
                  );
                })}
              </div>
            );
          })}
        </nav>
        <UserBox />
      </aside>
      {open && <div className="fixed inset-0 z-30 bg-black/30 lg:hidden" onClick={() => setOpen(false)} aria-hidden />}
      <div className="min-w-0">
        <header className="sticky top-[env(safe-area-inset-top)] z-20 flex h-14 items-center gap-3 border-b border-rule bg-sheet/90 px-4 backdrop-blur lg:hidden">
          <button onClick={() => setOpen(true)} className="rounded p-1 text-ink-2" aria-label="Open menu">
            <Menu className="size-5" />
          </button>
          <Logo />
        </header>
        <main className="mx-auto w-full max-w-[1400px] px-[max(16px,env(safe-area-inset-left))] py-6 lg:px-8">{children}</main>
      </div>
    </div>
  );
}

function ClientSwitcher() {
  const { clients, current, setCurrent, loading } = useClients();
  const { me } = useSession();
  if (loading) return <div className="mx-3 h-12 animate-pulse rounded-md bg-sunk" />;
  if (!clients.length)
    return (
      <Link href="/clients?new=1" className="mx-3 rounded-md border border-dashed border-rule-2 px-3 py-2.5 text-[13px] text-ink-2 hover:bg-sunk">
        + Add your first {me?.firm?.kind === "business" ? "GSTIN" : "client"}
      </Link>
    );
  return (
    <label className="relative mx-3 block rounded-md border border-rule bg-paper px-3 py-2 hover:border-rule-2">
      <span className="block text-[11px] font-semibold tracking-wider text-ink-3 uppercase">{me?.firm?.kind === "business" ? "Business" : "Client"}</span>
      <span className="flex items-center justify-between gap-2">
        <span className="truncate text-[13.5px] font-medium">{current?.name}</span>
        <ChevronsUpDown className="size-3.5 shrink-0 text-ink-3" />
      </span>
      <select
        className="absolute inset-0 cursor-pointer opacity-0"
        value={current?.id ?? ""}
        onChange={(e) => setCurrent(e.target.value)}
        aria-label="Switch client"
      >
        {clients.map((c) => (
          <option key={c.id} value={c.id}>
            {c.name}
          </option>
        ))}
      </select>
    </label>
  );
}

function UserBox() {
  const { me } = useSession();
  const qc = useQueryClient();
  const router = useRouter();
  async function logout() {
    await api("/api/auth/logout", { method: "POST" }).catch(() => undefined);
    qc.clear();
    router.replace("/login");
  }
  return (
    <div className="border-t border-rule p-3 pb-[max(12px,env(safe-area-inset-bottom))]">
      <div className="flex items-center gap-2.5">
        <div className="grid size-8 shrink-0 place-items-center rounded-full bg-accent-soft text-[13px] font-semibold text-accent">
          {me?.user.name.slice(0, 1).toUpperCase()}
        </div>
        <div className="min-w-0 flex-1">
          <div className="truncate text-[13px] font-medium">{me?.user.name}</div>
          <div className="truncate text-[12px] text-ink-3">
            {me?.firm?.name} · {me?.firm?.role}
          </div>
        </div>
        <button onClick={logout} className="rounded p-1.5 text-ink-3 hover:bg-sunk hover:text-ink" aria-label="Sign out" title="Sign out">
          <LogOut className="size-4" />
        </button>
      </div>
    </div>
  );
}
