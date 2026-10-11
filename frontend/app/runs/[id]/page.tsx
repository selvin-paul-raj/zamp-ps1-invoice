"use client";

import { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { CheckCircle2, CircleDashed, AlertTriangle, XCircle, MinusCircle, RotateCcw, Loader2, Info } from "lucide-react";
import { api, API, type Run, type RunEvent, type Finding } from "@/lib/api";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { DecisionBadge } from "@/components/decision-badge";
import { cn } from "@/lib/utils";

const PIPELINE = ["intake", "arithmetic", "vendor_match", "po_lookup", "currency", "three_way", "tax", "duplicates", "decide", "explain"];
const LABELS: Record<string, string> = {
  intake: "Intake & required fields", arithmetic: "Arithmetic validation", vendor_match: "Vendor master match", po_lookup: "PO lookup",
  currency: "Currency check", three_way: "3-way match (invoice / PO / GRN)", tax: "Tax validation", duplicates: "Duplicate detection",
  decide: "Decision (rules engine)", explain: "Explanation",
};

function StatusIcon({ status }: { status?: string }) {
  if (!status) return <CircleDashed className="h-5 w-5 text-stone-300" />;
  if (status === "pass") return <CheckCircle2 className="h-5 w-5 text-emerald-600" />;
  if (status === "warn") return <AlertTriangle className="h-5 w-5 text-amber-500" />;
  if (status === "fail") return <XCircle className="h-5 w-5 text-rose-600" />;
  return <MinusCircle className="h-5 w-5 text-stone-400" />;
}

function FindingRow({ f }: { f: Finding }) {
  const v = f.severity === "reject" ? "reject" : f.severity === "hold" ? "hold" : "muted";
  return (
    <div className="flex items-start gap-2 text-xs">
      <Badge variant={v} className="font-mono">{f.code}</Badge>
      <span className="text-stone-700">{f.message}</span>
    </div>
  );
}

export default function RunPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const [run, setRun] = useState<Run | null>(null);
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [live, setLive] = useState(true);

  useEffect(() => {
    if (!id) return;
    setEvents([]); setLive(true);
    api<Run>(`/runs/${id}`).then(setRun).catch(() => {});
    const es = new EventSource(`${API}/runs/${id}/stream`);
    const onEvent = (e: MessageEvent) => {
      const ev: RunEvent = JSON.parse(e.data);
      setEvents((prev) => (prev.some((p) => p.seq === ev.seq) ? prev : [...prev, ev]));
    };
    es.addEventListener("step", onEvent);
    es.addEventListener("done", (e) => { onEvent(e as MessageEvent); es.close(); setLive(false); api<Run>(`/runs/${id}`).then(setRun); });
    return () => es.close();
  }, [id]);

  const latestAttempt = Math.max(1, ...events.map((e) => e.attempt || 0));
  const byStep = new Map<string, RunEvent>();
  events.filter((e) => e.attempt === latestAttempt).forEach((e) => byStep.set(e.step, e));
  const retries = events.filter((e) => e.step === "retry");
  const running = live && !byStep.has("explain");
  const nextStep = PIPELINE.find((s) => !byStep.has(s));

  async function rerun() {
    if (!run) return;
    const r = await api<{ run_id: string }>(`/invoices/${run.invoice_id}/runs`, { method: "POST", headers: { "Idempotency-Key": `ui-${run.invoice_id}-${Date.now()}` } });
    router.push(`/runs/${r.run_id}`);
  }

  const decideData = byStep.get("decide")?.data?.data;
  const decision = decideData?.decision ?? run?.decision;
  const conf = decideData?.confidence ?? run?.confidence;
  const expected = run?.invoice.expected;

  return (
    <div className="grid gap-6 lg:grid-cols-[1fr_340px]">
      <div className="space-y-4">
        <div className="flex items-start justify-between gap-3">
          <div>
            <p className="text-xs text-muted-foreground">Run {id}{run ? ` · invoice ${run.invoice_id}` : ""}</p>
            <h1 className="text-xl font-semibold">{run?.invoice.vendor_name ?? "…"} <span className="font-mono text-base text-muted-foreground">{run?.invoice.invoice_number}</span></h1>
            {expected?.story && <p className="mt-1 text-sm text-muted-foreground">Scenario: {expected.story}</p>}
          </div>
          <div className="flex items-center gap-2">
            {running ? <Badge variant="hold"><Loader2 className="mr-1 h-3 w-3 animate-spin" />live</Badge> : <Badge variant="outline">finished</Badge>}
            <Button variant="outline" size="sm" onClick={rerun}><RotateCcw className="h-4 w-4" />Re-run</Button>
          </div>
        </div>

        {retries.length > 0 && (
          <div className="rounded-md border border-amber-200 bg-amber-50 p-3 text-xs text-amber-900">
            {retries.map((r) => <div key={r.seq}>Attempt {r.attempt}: {r.message}</div>)}
          </div>
        )}

        <Card>
          <CardHeader><CardTitle>Pipeline</CardTitle><CardDescription>Streamed step by step over Server-Sent Events</CardDescription></CardHeader>
          <CardContent>
            <ol className="relative space-y-1">
              {PIPELINE.map((s) => {
                const ev = byStep.get(s);
                const findings: Finding[] = ev?.data?.findings ?? [];
                const isNext = running && s === nextStep;
                return (
                  <li key={s} className={cn("flex gap-3 rounded-lg p-2 transition-colors", isNext && "bg-muted", ev && "animate-[fadein_.3s_ease]")}>
                    <div className="pt-0.5">{isNext ? <Loader2 className="h-5 w-5 animate-spin text-stone-500" /> : <StatusIcon status={ev?.status} />}</div>
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center justify-between gap-2">
                        <span className={cn("text-sm font-medium", !ev && "text-muted-foreground")}>{LABELS[s]}</span>
                        {ev && <span className="text-[10px] text-muted-foreground">{new Date(ev.ts).toLocaleTimeString()}</span>}
                      </div>
                      {ev && <p className="text-xs text-muted-foreground">{ev.message}</p>}
                      {findings.length > 0 && <div className="mt-1.5 space-y-1">{findings.map((f, i) => <FindingRow key={i} f={f} />)}</div>}
                      {s === "three_way" && ev && ev.data?.data?.lines?.length > 0 && (
                        <table className="mt-2 w-full text-[11px]">
                          <thead className="text-muted-foreground"><tr><th className="text-left">SKU</th><th>Inv qty</th><th>Prior billed</th><th>Received</th><th>PO qty</th><th>Inv price</th><th>PO price</th></tr></thead>
                          <tbody>{ev.data.data.lines.map((l: any) => (
                            <tr key={l.sku} className="text-center tabular-nums"><td className="text-left font-mono">{l.sku}</td><td>{l.inv_qty}</td><td>{l.prior_billed}</td><td>{l.received}</td><td>{l.po_qty}</td><td>{l.inv_price}</td><td>{l.po_price}</td></tr>
                          ))}</tbody>
                        </table>
                      )}
                    </div>
                  </li>
                );
              })}
            </ol>
          </CardContent>
        </Card>
      </div>

      <div className="space-y-4">
        <Card>
          <CardHeader><CardTitle>Decision</CardTitle></CardHeader>
          <CardContent className="space-y-3">
            <div className="flex items-center gap-3">
              <DecisionBadge decision={decision} />
              {conf != null && <span className="text-sm tabular-nums text-muted-foreground">confidence {Math.round(conf * 100)}%</span>}
            </div>
            {run?.final_decision && <p className="text-xs">Human review: <DecisionBadge decision={run.final_decision} /></p>}
            {run?.explanation && !running && <p className="text-sm leading-relaxed">{run.explanation}</p>}
            {expected && !running && (
              <p className="flex items-center gap-1 text-xs text-muted-foreground"><Info className="h-3 w-3" />Label: {expected.decision}{expected.reason ? ` / ${expected.reason}` : ""} {expected.decision === decision ? "✓" : "✗"}</p>
            )}
            {run?.review_status === "pending" && !running && <Button size="sm" variant="outline" onClick={() => router.push("/review")}>Open review queue</Button>}
          </CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle>Invoice</CardTitle></CardHeader>
          <CardContent className="space-y-1 text-xs">
            {run && (<>
              <Row k="PO on invoice" v={run.invoice.po_number || "— (none)"} />
              <Row k="Matched PO" v={run.matched_po || "–"} />
              <Row k="Vendor ID" v={run.vendor_id || "–"} />
              <Row k="Total" v={`${run.invoice.currency} ${Number(run.invoice.total ?? 0).toLocaleString()}`} />
              <Row k="Channel" v={run.invoice.source} />
              <Row k="Remit-to" v={run.invoice.payload?.bank_account || "–"} />
              {run.invoice.payload?.notes && <Row k="Notes" v={run.invoice.payload.notes} />}
            </>)}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}

function Row({ k, v }: { k: string; v: string }) {
  return <div className="flex justify-between gap-3"><span className="text-muted-foreground">{k}</span><span className="text-right font-mono">{v}</span></div>;
}
