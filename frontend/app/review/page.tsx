"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { api, runHref, type Run } from "@/lib/api";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";

function ReviewCard({ run, onDone }: { run: Run; onDone: () => void }) {
  const [note, setNote] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function act(action: "approve" | "reject" | "override", final_decision?: string) {
    setBusy(true); setErr(null);
    try {
      await api(`/runs/${run.id}/review`, { method: "POST", body: JSON.stringify({ action, note, reviewer: "reviewer", final_decision }) });
      onDone();
    } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  }

  return (
    <Card>
      <CardHeader>
        <div className="flex items-start justify-between gap-3">
          <div>
            <CardTitle>{run.invoice.vendor_name} · <span className="font-mono">{run.invoice.invoice_number}</span></CardTitle>
            <CardDescription>{run.invoice.currency} {Number(run.invoice.total ?? 0).toLocaleString()} · PO {run.matched_po || run.invoice.po_number || "none"} · <Link className="underline" href={runHref(run.id)}>open run</Link></CardDescription>
          </div>
          <Badge variant="hold">HOLD · {run.confidence != null ? Math.round(run.confidence * 100) : "–"}%</Badge>
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        {run.handoff && (
          <div className="rounded-md border border-amber-200 bg-amber-50 p-2 text-sm">
            <p className="font-medium">{run.handoff.question}</p>
            <p className="mt-1 text-xs text-amber-900">Who answers: {run.handoff.owner} · Next: {run.handoff.next_action}</p>
            {run.handoff.vendor_email_draft && (
              <details className="mt-1 text-xs"><summary className="cursor-pointer">Draft vendor email (not sent)</summary><pre className="mt-1 whitespace-pre-wrap font-sans">{run.handoff.vendor_email_draft}</pre></details>
            )}
          </div>
        )}
        <div className="space-y-1.5">
          {run.reasons.map((r, i) => (
            <div key={i} className="flex items-start gap-2 text-xs">
              <Badge variant={r.severity === "reject" ? "reject" : "hold"} className="font-mono">{r.code}</Badge>
              <span>{r.message}</span>
            </div>
          ))}
        </div>
        <input value={note} onChange={(e) => setNote(e.target.value)} placeholder="Reviewer note (required to approve)"
          className="w-full rounded-md border border-border px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-stone-300" />
        {err && <p className="text-xs text-rose-700">{err}</p>}
        <div className="flex flex-wrap gap-2">
          <Button size="sm" variant="success" disabled={busy} onClick={() => act("approve")}>Approve</Button>
          <Button size="sm" variant="destructive" disabled={busy} onClick={() => act("reject")}>Reject</Button>
          <Button size="sm" variant="outline" disabled={busy} onClick={() => act("override", "HOLD_FOR_REVIEW")}>Keep on hold (resolve)</Button>
        </div>
      </CardContent>
    </Card>
  );
}

export default function ReviewQueue() {
  const [items, setItems] = useState<Run[]>([]);
  const load = useCallback(() => api<Run[]>("/review-queue").then(setItems).catch(() => {}), []);
  useEffect(() => { load(); const t = setInterval(load, 3000); return () => clearInterval(t); }, [load]);

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-semibold">Review queue</h1>
        <p className="text-sm text-muted-foreground">Invoices the engine would not auto-decide. Each one arrives pre-investigated with one specific question and who should answer it. Every action is written to the audit trail with the reviewer’s note.</p>
      </div>
      {items.length === 0 && <p className="text-sm text-muted-foreground">Nothing waiting for review.</p>}
      <div className="grid gap-4 md:grid-cols-2">{items.map((r) => <ReviewCard key={r.id} run={r} onDone={load} />)}</div>
    </div>
  );
}
