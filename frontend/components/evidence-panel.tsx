"use client";

import { useState } from "react";
import { Database, Sparkles } from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import type { EvidenceRec, ModelCall } from "@/lib/api";

const RESULT: Record<string, { label: string; v: "approve" | "hold" | "reject" | "muted" }> = {
  match: { label: "match", v: "approve" },
  mismatch: { label: "mismatch", v: "hold" },
  missing: { label: "missing", v: "reject" },
  info: { label: "info", v: "muted" },
};

const fmt = (x: unknown) => (x === null || x === undefined || x === "" ? "–" : typeof x === "object" ? JSON.stringify(x) : String(x));

export function EvidencePanel({ evidence }: { evidence: EvidenceRec[] }) {
  const [onlyIssues, setOnlyIssues] = useState(false);
  if (!evidence?.length) return null;
  const rows = onlyIssues ? evidence.filter((e) => e.result === "mismatch" || e.result === "missing") : evidence;
  const sources = Array.from(new Set(evidence.map((e) => e.source_name)));
  const issues = evidence.filter((e) => e.result === "mismatch" || e.result === "missing").length;
  return (
    <Card>
      <CardHeader>
        <div className="flex items-start justify-between gap-3">
          <div>
            <CardTitle className="flex items-center gap-2"><Database className="h-4 w-4" />Evidence</CardTitle>
            <CardDescription>
              {evidence.length} checks against {sources.length} systems of record · {issues} issue{issues === 1 ? "" : "s"}. Every decision cites these records.
            </CardDescription>
          </div>
          <label className="flex shrink-0 items-center gap-1 text-xs text-muted-foreground">
            <input type="checkbox" checked={onlyIssues} onChange={(e) => setOnlyIssues(e.target.checked)} /> issues only
          </label>
        </div>
      </CardHeader>
      <CardContent className="overflow-x-auto">
        <table className="w-full text-[11px]">
          <thead className="text-left text-muted-foreground">
            <tr><th className="py-1 pr-2">Result</th><th className="pr-2">Source of truth</th><th className="pr-2">Record · field</th><th className="pr-2">Source says</th><th className="pr-2">Invoice says</th><th className="pr-2">Rule</th></tr>
          </thead>
          <tbody>
            {rows.map((e, i) => {
              const r = RESULT[e.result] ?? RESULT.info;
              return (
                <tr key={i} className={cn("border-t border-border align-top", (e.result === "mismatch" || e.result === "missing") && "bg-amber-50/60")}>
                  <td className="py-1 pr-2"><Badge variant={r.v}>{r.label}</Badge></td>
                  <td className="pr-2">
                    <div className="font-medium">{e.source_name}</div>
                    {e.source_as_of && <div className="text-[10px] text-muted-foreground">as of {new Date(e.source_as_of).toLocaleString()}</div>}
                  </td>
                  <td className="pr-2"><span className="font-mono">{fmt(e.record_id)}</span><div className="text-muted-foreground">{e.field}</div>
                    {e.doc_ref && <div className="text-[10px] text-muted-foreground" title={e.doc_ref}>read from: {e.doc_ref.length > 70 ? e.doc_ref.slice(0, 70) + "…" : e.doc_ref}</div>}
                  </td>
                  <td className="pr-2 font-mono">{fmt(e.expected)}</td>
                  <td className="pr-2 font-mono">{fmt(e.actual)}</td>
                  <td className="pr-2"><span className="font-mono">{e.rule_id}</span>{e.finding_code && <div className="font-mono text-amber-700">{e.finding_code}</div>}
                    {e.note && <div className="text-[10px] text-muted-foreground">{e.note}</div>}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </CardContent>
    </Card>
  );
}

const ROLE: Record<string, string> = {
  duplicate_judgement: "Is this a re-submission of an earlier invoice?",
  choose_po: "Which open PO does this invoice belong to?",
  field_check: "Does the document text support the extracted value?",
};

export function ModelCallsCard({ calls }: { calls: ModelCall[] }) {
  if (!calls?.length) return null;
  const tokens = calls.reduce((a, c) => a + (c.input_tokens || 0), 0);
  const cost = calls.reduce((a, c) => a + (c.cost_usd || 0), 0);
  const modes = Array.from(new Set(calls.map((c) => c.mode)));
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2"><Sparkles className="h-4 w-4" />Jev model (advisory)</CardTitle>
        <CardDescription>
          Typed questions with probabilities. A model can add a hold or a suggestion, never an approval. Mode: {modes.join(", ")}
          {modes.includes("replay") ? " (recorded real Jev answers, no live call)" : ""}{modes.includes("fake") ? " (deterministic stand-in)" : ""}.
          {tokens ? ` ${tokens} tokens, $${cost.toFixed(6)}.` : ""}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-2 text-xs">
        {calls.map((c, i) => (
          <div key={i} className="rounded-md border border-border p-2">
            <div className="flex items-center justify-between gap-2">
              <span className="font-medium">{ROLE[c.role] ?? c.role}</span>
              <Badge variant={c.outcome === "hold_added" ? "hold" : c.outcome === "suggestion" ? "approve" : "muted"}>{c.outcome.replaceAll("_", " ")}</Badge>
            </div>
            <div className="mt-1 text-muted-foreground">
              answer <span className="font-mono text-foreground">{fmt(c.answer)}</span>
              {c.probability != null && <> · p = <span className="font-mono text-foreground">{c.probability.toFixed(2)}</span></>}
              {c.threshold != null && <> · gate {c.threshold.toFixed(2)}</>}
            </div>
            {c.options != null && typeof c.options === "object" && !Array.isArray(c.options) ? (
              <div className="mt-1 flex flex-wrap gap-1">{Object.entries(c.options as Record<string, number>).map(([k, v]) => <span key={k} className="rounded bg-muted px-1 font-mono">{k}: {Number(v).toFixed(2)}</span>)}</div>
            ) : null}
            <div className="mt-1 text-[10px] text-muted-foreground">{c.model || "–"} · {c.mode} · {c.latency_ms} ms · {c.input_tokens} tokens{c.error ? ` · ${c.error}` : ""}</div>
          </div>
        ))}
      </CardContent>
    </Card>
  );
}
