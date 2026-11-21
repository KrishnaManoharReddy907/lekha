"use client";

import { useQuery } from "@tanstack/react-query";
import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { api } from "./api";
import type { Client } from "./types";

interface ClientCtxValue {
  clients: Client[];
  loading: boolean;
  current: Client | null;
  setCurrent: (id: string | null) => void;
}

const Ctx = createContext<ClientCtxValue | null>(null);
const KEY = "lekha.currentClient";

export function ClientProvider({ children }: { children: ReactNode }) {
  const q = useQuery({ queryKey: ["clients"], queryFn: () => api<Client[]>("/api/clients") });
  const [id, setId] = useState<string | null>(null);

  useEffect(() => {
    try {
      setId(localStorage.getItem(KEY));
    } catch {
      /* storage unavailable */
    }
  }, []);

  const current = useMemo(() => {
    const list = q.data ?? [];
    return list.find((c) => c.id === id) ?? list[0] ?? null;
  }, [q.data, id]);

  const value: ClientCtxValue = {
    clients: q.data ?? [],
    loading: q.isLoading,
    current,
    setCurrent: (next) => {
      setId(next);
      try {
        if (next) localStorage.setItem(KEY, next);
        else localStorage.removeItem(KEY);
      } catch {
        /* ignore */
      }
    },
  };
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useClients(): ClientCtxValue {
  const v = useContext(Ctx);
  if (!v) throw new Error("useClients outside ClientProvider");
  return v;
}
