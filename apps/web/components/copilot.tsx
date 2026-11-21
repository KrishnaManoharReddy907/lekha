"use client";

import { useMutation } from "@tanstack/react-query";
import { MessageSquareText, Send, Sparkles, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { api, errorMessage } from "@/lib/api";
import { useClients } from "@/lib/clients";
import { Button, Select, cx } from "./ui";

interface Msg { role: "user" | "assistant"; content: string; tools?: { tool: string }[] }

const SUGGESTIONS = [
  "How much credit is at risk and why?",
  "Which suppliers are costing us the most credit?",
  "What deadlines are coming up?",
  "Explain the biggest mismatch this month",
];

/** Floating copilot: plain-language questions answered from read-only tools over this client's data. */
export function Copilot() {
  const { current } = useClients();
  const [open, setOpen] = useState(false);
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [conv, setConv] = useState<string | null>(null);
  const [text, setText] = useState("");
  const [lang, setLang] = useState("en");
  const end = useRef<HTMLDivElement>(null);
  useEffect(() => { setMsgs([]); setConv(null); }, [current?.id]);
  useEffect(() => end.current?.scrollIntoView({ behavior: "smooth" }), [msgs]);
  const ask = useMutation({
    mutationFn: (message: string) => api<{ answer: string; conversation_id: string; tool_calls: { tool: string }[] }>("/api/copilot", { body: { client_id: current?.id, message, conversation_id: conv, language: lang } }),
    onSuccess: (r) => { setConv(r.conversation_id); setMsgs((m) => [...m, { role: "assistant", content: r.answer, tools: r.tool_calls }]); },
    onError: (e) => setMsgs((m) => [...m, { role: "assistant", content: errorMessage(e) }]),
  });
  function send(q: string) {
    const t = q.trim();
    if (!t || !current || ask.isPending) return;
    setMsgs((m) => [...m, { role: "user", content: t }]);
    setText("");
    ask.mutate(t);
  }
  if (!current) return null;
  return (
    <>
      <button
        onClick={() => setOpen((o) => !o)}
        className="fixed right-[max(20px,env(safe-area-inset-right))] bottom-[max(20px,env(safe-area-inset-bottom))] z-40 inline-flex h-12 items-center gap-2 rounded-full bg-accent px-4 font-medium text-accent-ink shadow-card hover:brightness-110"
        aria-expanded={open}
        aria-label="Ask Lekha"
      >
        {open ? <X className="size-5" /> : <MessageSquareText className="size-5" />}
        <span className="hidden sm:inline">{open ? "Close" : "Ask Lekha"}</span>
      </button>
      {open && (
        <div className="fixed right-[max(16px,env(safe-area-inset-right))] bottom-[calc(max(20px,env(safe-area-inset-bottom))+60px)] z-40 flex h-[min(620px,calc(100dvh-120px))] w-[min(420px,calc(100vw-32px))] flex-col overflow-hidden rounded-xl border border-rule bg-sheet shadow-card" role="dialog" aria-label="Lekha copilot">
          <div className="flex items-center gap-2 border-b border-rule px-4 py-3">
            <Sparkles className="size-4 text-accent" />
            <div className="min-w-0 flex-1">
              <div className="text-[14px] font-semibold">Ask Lekha</div>
              <div className="truncate text-[12px] text-ink-3">About {current.name} · read-only</div>
            </div>
            <Select aria-label="Answer language" value={lang} onChange={(e) => setLang(e.target.value)} className="h-8 w-28 text-[12.5px]">
              <option value="en">English</option>
              <option value="hi">हिन्दी</option>
              <option value="te">తెలుగు</option>
            </Select>
          </div>
          <div className="flex-1 overflow-y-auto px-4 py-3">
            {msgs.length === 0 ? (
              <div className="flex flex-col gap-2">
                <p className="text-[13px] text-ink-3">Ask about credit at risk, suppliers, deadlines or any invoice. Answers come from your data.</p>
                {SUGGESTIONS.map((s) => (
                  <button key={s} onClick={() => send(s)} className="rounded-md border border-rule px-3 py-2 text-left text-[13px] hover:border-accent hover:bg-accent-soft/40">{s}</button>
                ))}
              </div>
            ) : (
              <div className="flex flex-col gap-3">
                {msgs.map((m, i) => (
                  <div key={i} className={cx("max-w-[90%] rounded-lg px-3 py-2 text-[13.5px] whitespace-pre-wrap", m.role === "user" ? "self-end bg-accent text-accent-ink" : "self-start bg-sunk text-ink")}>
                    {m.role === "assistant" ? <RichText text={m.content} /> : m.content}
                    {m.tools && m.tools.length > 0 && <div className="mt-1.5 text-[11px] text-ink-3">Looked up: {m.tools.map((t) => t.tool.replace("_", " ")).join(", ")}</div>}
                  </div>
                ))}
                {ask.isPending && <div className="self-start rounded-lg bg-sunk px-3 py-2 text-[13px] text-ink-3">Looking it up…</div>}
                <div ref={end} />
              </div>
            )}
          </div>
          <form className="flex items-end gap-2 border-t border-rule p-3" onSubmit={(e) => { e.preventDefault(); send(text); }}>
            <textarea
              aria-label="Your question"
              rows={1}
              value={text}
              onChange={(e) => setText(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(text); } }}
              placeholder="Ask a question…"
              className="max-h-32 min-h-9 flex-1 resize-none rounded-md border border-rule-2 bg-sheet px-3 py-2 text-[13.5px] outline-none focus:border-accent"
            />
            <Button type="submit" variant="primary" disabled={!text.trim()} loading={ask.isPending} aria-label="Send"><Send className="size-4" /></Button>
          </form>
        </div>
      )}
    </>
  );
}

/** Renders **bold** safely (no HTML injection) — models sometimes add it despite instructions. */
function RichText({ text }: { text: string }) {
  const parts = text.split(/(\*\*[^*]+\*\*)/g);
  return <>{parts.map((p, i) => (p.startsWith("**") && p.endsWith("**") ? <strong key={i}>{p.slice(2, -2)}</strong> : <span key={i}>{p}</span>))}</>;
}
