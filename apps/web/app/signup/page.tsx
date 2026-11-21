"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { AuthShell } from "@/components/auth-shell";
import { Button, Field, Input, cx } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { useAcceptMe } from "@/lib/session";
import type { Me } from "@/lib/types";

export default function SignupPage() {
  const router = useRouter();
  const accept = useAcceptMe();
  const [form, setForm] = useState({ name: "", email: "", password: "", firm_name: "", firm_kind: "ca_firm" });
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const set = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement>) => setForm({ ...form, [k]: e.target.value });

  const pwChecks = [
    { ok: form.password.length >= 10, label: "10+ characters" },
    { ok: /[a-z]/.test(form.password) && /[A-Z]/.test(form.password), label: "upper and lower case" },
    { ok: /\d/.test(form.password), label: "a digit" },
  ];

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      const me = await api<Me>("/api/auth/signup", { body: form });
      accept(me);
      router.replace("/dashboard?welcome=1");
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setLoading(false);
    }
  }

  return (
    <AuthShell
      title="Create your workspace"
      sub="Free to start. Your data stays in your workspace only."
      footer={
        <>
          Already have an account?{" "}
          <Link href="/login" className="font-medium text-accent">
            Sign in
          </Link>
        </>
      }
    >
      <form onSubmit={submit} className="flex flex-col gap-4">
        <div className="grid grid-cols-2 gap-2" role="radiogroup" aria-label="Workspace type">
          {[
            ["ca_firm", "CA / accounting firm", "Manage many clients"],
            ["business", "My business", "One company's books"],
          ].map(([v, t, s]) => (
            <button
              type="button"
              key={v}
              role="radio"
              aria-checked={form.firm_kind === v}
              onClick={() => setForm({ ...form, firm_kind: v })}
              className={cx("rounded-md border p-3 text-left transition", form.firm_kind === v ? "border-accent bg-accent-soft" : "border-rule-2 bg-sheet hover:bg-sunk")}
            >
              <span className="block text-[13.5px] font-medium">{t}</span>
              <span className="block text-[12px] text-ink-3">{s}</span>
            </button>
          ))}
        </div>
        <Field label="Your name" htmlFor="name">
          <Input id="name" required value={form.name} onChange={set("name")} autoComplete="name" />
        </Field>
        <Field label={form.firm_kind === "ca_firm" ? "Firm name" : "Business name"} htmlFor="firm">
          <Input id="firm" required value={form.firm_name} onChange={set("firm_name")} placeholder={form.firm_kind === "ca_firm" ? "Rao & Associates" : "Deccan FurnitureWorks Pvt Ltd"} />
        </Field>
        <Field label="Work email" htmlFor="email">
          <Input id="email" type="email" required value={form.email} onChange={set("email")} autoComplete="email" />
        </Field>
        <Field
          label="Password"
          htmlFor="password"
          hint={
            <span className="flex flex-wrap gap-x-3">
              {pwChecks.map((c) => (
                <span key={c.label} className={c.ok ? "text-ok" : ""}>
                  {c.ok ? "✓" : "·"} {c.label}
                </span>
              ))}
            </span>
          }
        >
          <Input id="password" type="password" required value={form.password} onChange={set("password")} autoComplete="new-password" />
        </Field>
        {error ? <p className="rounded-md bg-red-soft px-3 py-2 text-[13px] text-red">{error}</p> : null}
        <Button type="submit" variant="primary" loading={loading} className="mt-1">
          Create workspace
        </Button>
      </form>
    </AuthShell>
  );
}
