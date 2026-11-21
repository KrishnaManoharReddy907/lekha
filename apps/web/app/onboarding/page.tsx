"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { AuthShell } from "@/components/auth-shell";
import { Button, Field, Input, Select } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { SessionProvider, useAcceptMe } from "@/lib/session";
import type { Me } from "@/lib/types";

function Form() {
  const router = useRouter();
  const accept = useAcceptMe();
  const [name, setName] = useState("");
  const [kind, setKind] = useState("ca_firm");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  return (
    <form
      className="flex flex-col gap-4"
      onSubmit={async (e) => {
        e.preventDefault();
        setLoading(true);
        setError(null);
        try {
          await api<Me>("/api/auth/me").then((m) => accept(m));
          const me = await api<Me>("/api/auth/firms", { body: { name, kind } });
          accept(me);
          router.replace("/dashboard?welcome=1");
        } catch (err) {
          setError(errorMessage(err));
        } finally {
          setLoading(false);
        }
      }}
    >
      <Field label="Workspace type" htmlFor="kind">
        <Select id="kind" value={kind} onChange={(e) => setKind(e.target.value)}>
          <option value="ca_firm">CA / accounting firm</option>
          <option value="business">My business</option>
        </Select>
      </Field>
      <Field label="Name" htmlFor="name">
        <Input id="name" required value={name} onChange={(e) => setName(e.target.value)} />
      </Field>
      {error && <p className="rounded-md bg-red-soft px-3 py-2 text-[13px] text-red">{error}</p>}
      <Button type="submit" variant="primary" loading={loading}>Create workspace</Button>
    </form>
  );
}

export default function Onboarding() {
  return (
    <SessionProvider>
      <AuthShell title="Create a workspace" sub="You are signed in but not part of a workspace yet.">
        <Form />
      </AuthShell>
    </SessionProvider>
  );
}
