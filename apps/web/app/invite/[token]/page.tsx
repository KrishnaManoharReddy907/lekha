"use client";

import { useQuery } from "@tanstack/react-query";
import { useParams, useRouter } from "next/navigation";
import { useState } from "react";
import { AuthShell } from "@/components/auth-shell";
import { Button, Field, Input, Spinner } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { useAcceptMe } from "@/lib/session";
import type { Me } from "@/lib/types";

export default function InvitePage() {
  const { token } = useParams<{ token: string }>();
  const router = useRouter();
  const accept = useAcceptMe();
  const info = useQuery({ queryKey: ["invite", token], queryFn: () => api<{ email: string; role: string; firm_name: string }>(`/api/auth/invite/${token}`), retry: false });
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  if (info.isLoading) return <div className="grid min-h-dvh place-items-center"><Spinner className="size-6" /></div>;
  if (info.isError)
    return (
      <AuthShell title="Invitation not valid" sub={errorMessage(info.error)}>
        <Button onClick={() => router.push("/login")}>Go to sign in</Button>
      </AuthShell>
    );
  const inv = info.data!;
  return (
    <AuthShell title={`Join ${inv.firm_name}`} sub={`You were invited as ${inv.role} (${inv.email}).`}>
      <form
        className="flex flex-col gap-4"
        onSubmit={async (e) => {
          e.preventDefault();
          setLoading(true);
          setError(null);
          try {
            const me = await api<Me>("/api/auth/signup", { body: { name, email: inv.email, password, invite_token: token } });
            accept(me);
            router.replace("/dashboard");
          } catch (err) {
            setError(errorMessage(err));
          } finally {
            setLoading(false);
          }
        }}
      >
        <Field label="Your name" htmlFor="name"><Input id="name" required value={name} onChange={(e) => setName(e.target.value)} /></Field>
        <Field label="Choose a password" htmlFor="pw" hint="10+ characters with upper, lower case and a digit"><Input id="pw" type="password" required value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="new-password" /></Field>
        {error && <p className="rounded-md bg-red-soft px-3 py-2 text-[13px] text-red">{error}</p>}
        <Button type="submit" variant="primary" loading={loading}>Join workspace</Button>
        <p className="text-[13px] text-ink-3">Already have an account? Sign in first, then open this link again.</p>
      </form>
    </AuthShell>
  );
}
