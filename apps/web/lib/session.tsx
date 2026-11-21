"use client";

import { QueryClient, QueryClientProvider, useQuery, useQueryClient } from "@tanstack/react-query";
import { createContext, useContext, useState, type ReactNode } from "react";
import { Toaster } from "sonner";
import { ApiError, api, setCsrf } from "./api";
import type { Me } from "./types";

export function Providers({ children }: { children: ReactNode }) {
  const [qc] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 15_000,
            retry: (count, err) => !(err instanceof ApiError && err.status < 500) && count < 2,
            refetchOnWindowFocus: false,
          },
        },
      }),
  );
  return (
    <QueryClientProvider client={qc}>
      {children}
      <Toaster position="bottom-right" richColors closeButton />
    </QueryClientProvider>
  );
}

interface SessionValue {
  me: Me | undefined;
  loading: boolean;
  error: unknown;
  setMe: (me: Me) => void;
  can: (action: "write" | "admin") => boolean;
}

const SessionCtx = createContext<SessionValue | null>(null);

export function SessionProvider({ children }: { children: ReactNode }) {
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: ["me"],
    queryFn: async () => {
      const me = await api<Me>("/api/auth/me");
      setCsrf(me.csrf_token);
      return me;
    },
    retry: false,
  });
  const value: SessionValue = {
    me: q.data,
    loading: q.isLoading,
    error: q.error,
    setMe: (me) => {
      setCsrf(me.csrf_token);
      qc.setQueryData(["me"], me);
    },
    can: (action) => {
      const role = q.data?.firm?.role;
      if (!role) return false;
      if (action === "admin") return role === "owner" || role === "partner";
      return role !== "viewer";
    },
  };
  return <SessionCtx.Provider value={value}>{children}</SessionCtx.Provider>;
}

export function useSession(): SessionValue {
  const v = useContext(SessionCtx);
  if (!v) throw new Error("useSession outside SessionProvider");
  return v;
}

/** Stores a fresh /me response (after login/signup) and the CSRF token that comes with it. */
export function useAcceptMe() {
  const qc = useQueryClient();
  return (me: Me) => {
    setCsrf(me.csrf_token);
    qc.setQueryData(["me"], me);
  };
}
