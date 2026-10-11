"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { Play, Upload, RefreshCw } from "lucide-react";
import { api, API, type Run, type Stats } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { DecisionBadge } from "@/components/decision-badge";
import { Badge } from "@/components/ui/badge";

function Kpi({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <Card>
      <CardHeader className="pb-1"><CardDescription>{label}</CardDescription></CardHeader>
      <CardContent><div className="text-2xl font-semibold tabular-nums">{value}</div>{hint && <p className="mt-1 text-xs text-muted-foreground">{hint}</p>}</CardContent>
    </Card>
  );
}

const pct = (x: number | null | undefined) => (x == null ? "–" : `${Math.round(x * 100)}%`);

export default function Dashboard() {
  const [runs, setRuns] = useState<Run[]>([]);
  const [stats, setStats] = useState<Stats | null>(null);
  const [filter, setFilter] = useState<string>("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const load = useCallback(async () => {
    try {
      const [r, s] = await Promise.all([api<Run[]>(`/runs${filter ? `?decision=${filter}` : ""}`), api<Stats>("/stats")]);
      setRuns(r); setStats(s); setError(null);
    } catch (e) { setError(`Backend not reachable at ${API}: ${(e as Error).message}`); }
  }, [filter]);

  useEffect(() => { load(); const t = setInterval(load, 2000); return () => clearInterval(t); }, [load]);

  async function seed() {
    setBusy(true);
    try { await api("/demo/seed", { method: "POST" }); await load(); } finally { setBusy(false); }
  }

  async function upload(f: File) {
    const fd = new FormData(); fd.append("file", f);
    const res = await fetch(`${API}/invoices/upload`, { method: "POST", body: fd });
    if (!res.ok) { setError(await res.text()); return; }
    const body = await res.json();
    const first = body.results?.[0];
    if (first?.run_id) window.location.href = `/runs/${first.run_id}`;
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">Run history</h1>
          <p className="text-sm text-muted-foreground">Every invoice run, its decision, confidence and primary reason. Click a row for the live step timeline.</p>
        </div>
        <div className="flex gap-2">
          <input ref={fileRef} type="file" accept=".json,.csv,.pdf,.txt" className="hidden" onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])} />
          <Button variant="outline" onClick={() => fileRef.current?.click()}><Upload className="h-4 w-4" />Upload invoice</Button>
          <Button variant="outline" onClick={load}><RefreshCw className="h-4 w-4" /></Button>
          <Button onClick={seed} disabled={busy}><Play className="h-4 w-4" />{busy ? "Seeding…" : "Run demo seed"}</Button>
        </div>
      </div>
      {error && <div className="rounded-md border border-rose-200 bg-rose-50 p-3 text-sm text-rose-800">{error}</div>}

      <div className="grid grid-cols-2 gap-4 md:grid-cols-5">
        <Kpi label="Invoices processed" value={`${stats?.completed ?? 0}/${stats?.invoices ?? 0}`} />
        <Kpi label="Straight-through (auto-approve)" value={pct(stats?.straight_through_rate)} />
        <Kpi label="Pending review" value={`${stats?.pending_review ?? 0}`} hint="HOLD_FOR_REVIEW without a human decision" />
        <Kpi label="Accuracy vs labels" value={pct(stats?.accuracy_vs_labels)} hint={`${stats?.labelled ?? 0} labelled seed invoices`} />
        <Kpi label="False approvals" value={`${stats?.false_approvals ?? 0}`} hint="Bad invoices auto-approved" />
      </div>

      <Card>
        <CardHeader className="flex-row items-center justify-between">
          <CardTitle>Runs</CardTitle>
          <div className="flex gap-1">
            {["", "APPROVE", "HOLD_FOR_REVIEW", "REJECT"].map((d) => (
              <Button key={d} size="sm" variant={filter === d ? "default" : "outline"} onClick={() => setFilter(d)}>{d === "" ? "All" : d === "HOLD_FOR_REVIEW" ? "Hold" : d[0] + d.slice(1).toLowerCase()}</Button>
            ))}
          </div>
        </CardHeader>
        <CardContent className="p-0">
          <Table>
            <THead><TR><TH>Received</TH><TH>Vendor</TH><TH>Invoice #</TH><TH>PO</TH><TH className="text-right">Total</TH><TH>Decision</TH><TH>Conf.</TH><TH>Primary reason</TH><TH>Review</TH><TH>Expected</TH></TR></THead>
            <TBody>
              {runs.map((r) => (
                <TR key={r.id} className="cursor-pointer" onClick={() => (window.location.href = `/runs/${r.id}`)}>
                  <TD className="whitespace-nowrap text-xs text-muted-foreground">{new Date(r.invoice.received_at).toLocaleString()}</TD>
                  <TD className="max-w-48 truncate">{r.invoice.vendor_name}</TD>
                  <TD className="font-mono text-xs"><Link href={`/runs/${r.id}`}>{r.invoice.invoice_number}</Link></TD>
                  <TD className="font-mono text-xs">{r.matched_po || r.invoice.po_number || "–"}</TD>
                  <TD className="text-right tabular-nums">{r.invoice.currency} {Number(r.invoice.total ?? 0).toLocaleString()}</TD>
                  <TD><DecisionBadge decision={r.status === "completed" ? r.effective_decision : null} /></TD>
                  <TD className="tabular-nums">{r.confidence != null ? `${Math.round(r.confidence * 100)}%` : "–"}</TD>
                  <TD className="font-mono text-xs">{r.primary_reason || "–"}</TD>
                  <TD>{r.review_status === "pending" ? <Badge variant="hold">pending</Badge> : r.review_status === "resolved" ? <Badge variant="outline">resolved</Badge> : <span className="text-xs text-muted-foreground">–</span>}</TD>
                  <TD className="text-xs text-muted-foreground">{r.invoice.expected ? (r.invoice.expected.decision === r.decision ? "✓ " : "✗ ") + (r.invoice.expected.decision === "HOLD_FOR_REVIEW" ? "HOLD" : r.invoice.expected.decision) : "–"}</TD>
                </TR>
              ))}
              {runs.length === 0 && <TR><TD colSpan={10} className="py-10 text-center text-muted-foreground">No runs yet. Click “Run demo seed” or upload an invoice (JSON / CSV).</TD></TR>}
            </TBody>
          </Table>
        </CardContent>
      </Card>

      {stats && stats.top_reasons.length > 0 && (
        <Card>
          <CardHeader><CardTitle>Exception reasons</CardTitle><CardDescription>Primary reason across held and rejected invoices</CardDescription></CardHeader>
          <CardContent className="space-y-2">
            {stats.top_reasons.map(([code, n]) => (
              <div key={code} className="flex items-center gap-3 text-sm">
                <span className="w-56 font-mono text-xs">{code}</span>
                <div className="h-2 rounded bg-amber-400" style={{ width: `${(n / Math.max(...stats.top_reasons.map((x) => x[1]))) * 240}px` }} />
                <span className="tabular-nums text-muted-foreground">{n}</span>
              </div>
            ))}
          </CardContent>
        </Card>
      )}
    </div>
  );
}
