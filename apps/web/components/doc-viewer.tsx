"use client";

import { Minus, Plus, ScanText } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import type { OcrToken } from "@/lib/types";
import { cx } from "./ui";

export interface Highlight {
  page: number; // 1-based
  bbox: [number, number, number, number];
  tone?: "accent" | "warn" | "bad";
}

/**
 * Shows the document's page images with OCR token boxes on top.
 * - `highlight` draws the evidence box of the selected field and scrolls it into view.
 * - Clicking a token calls `onPick(text)` so the reviewer can fill a field straight from the page.
 */
export function DocViewer({
  pages,
  tokens,
  highlight,
  onPick,
  pickLabel,
}: {
  pages: string[];
  tokens: OcrToken[][];
  highlight: Highlight | null;
  onPick?: (text: string) => void;
  pickLabel?: string | null;
}) {
  const [zoom, setZoom] = useState(1);
  const [showBoxes, setShowBoxes] = useState(false);
  const hlRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    hlRef.current?.scrollIntoView({ block: "center", inline: "center", behavior: "smooth" });
  }, [highlight]);

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex items-center justify-between gap-2 border-b border-rule bg-sheet px-3 py-2">
        <div className="text-[12.5px] text-ink-3">
          {pickLabel && onPick ? (
            <span>
              Click text on the page to fill <span className="font-medium text-accent">{pickLabel}</span>
            </span>
          ) : (
            <span>{pages.length} page{pages.length === 1 ? "" : "s"}</span>
          )}
        </div>
        <div className="flex items-center gap-1">
          <button
            onClick={() => setShowBoxes((v) => !v)}
            className={cx("rounded p-1.5 hover:bg-sunk", showBoxes ? "text-accent" : "text-ink-3")}
            title="Show all text Lekha read"
            aria-pressed={showBoxes}
          >
            <ScanText className="size-4" />
          </button>
          <button onClick={() => setZoom((z) => Math.max(0.5, z - 0.25))} className="rounded p-1.5 text-ink-3 hover:bg-sunk" aria-label="Zoom out">
            <Minus className="size-4" />
          </button>
          <span className="num w-10 text-center text-[12px] text-ink-2">{Math.round(zoom * 100)}%</span>
          <button onClick={() => setZoom((z) => Math.min(3, z + 0.25))} className="rounded p-1.5 text-ink-3 hover:bg-sunk" aria-label="Zoom in">
            <Plus className="size-4" />
          </button>
        </div>
      </div>
      <div className="min-h-0 flex-1 overflow-auto bg-sunk p-4">
        <div className="mx-auto flex flex-col gap-4" style={{ width: `${zoom * 100}%`, maxWidth: zoom <= 1 ? 820 : undefined }}>
          {pages.map((src, pi) => (
            <div key={src} className="relative overflow-hidden rounded border border-rule bg-white shadow-card">
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src={src} alt={`Page ${pi + 1}`} className="block w-full select-none" draggable={false} />
              {(tokens[pi] ?? []).map((t) => (
                <button
                  key={t.id}
                  type="button"
                  onClick={() => onPick?.(t.text)}
                  title={t.text}
                  className={cx(
                    "absolute rounded-[2px] transition",
                    onPick && pickLabel ? "cursor-copy hover:bg-accent/25 hover:outline hover:outline-1 hover:outline-accent" : "cursor-default",
                    showBoxes ? "bg-blue/10 outline outline-1 outline-blue/40" : "",
                  )}
                  style={{
                    left: `${t.box[0] * 100}%`,
                    top: `${t.box[1] * 100}%`,
                    width: `${(t.box[2] - t.box[0]) * 100}%`,
                    height: `${(t.box[3] - t.box[1]) * 100}%`,
                  }}
                  tabIndex={-1}
                  aria-hidden
                />
              ))}
              {highlight && highlight.page === pi + 1 && (
                <div
                  ref={hlRef}
                  className={cx(
                    "pointer-events-none absolute rounded-[3px] ring-2",
                    highlight.tone === "bad" ? "bg-red/15 ring-red" : highlight.tone === "warn" ? "bg-amber/15 ring-amber" : "bg-accent/15 ring-accent",
                  )}
                  style={{
                    left: `calc(${highlight.bbox[0] * 100}% - 3px)`,
                    top: `calc(${highlight.bbox[1] * 100}% - 3px)`,
                    width: `calc(${(highlight.bbox[2] - highlight.bbox[0]) * 100}% + 6px)`,
                    height: `calc(${(highlight.bbox[3] - highlight.bbox[1]) * 100}% + 6px)`,
                  }}
                />
              )}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
