"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { Play, Upload, RefreshCw, FileText, Download, IndianRupee } from "lucide-react";
import { api, API, runHref, type Impact, type Run, type Sample, type Stats } from "@/lib/api";
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
const inr = (x: number) => (x >= 100000 ? `₹${(x / 100000).toFixed(x >= 1000000 ? 1 : 2)} lakh` : `₹${Math.round(x).toLocaleString("en-IN")}`);

function ImpactCard() {
  const [a, setA] = useState({ monthly_invoices: 1000, manual_minutes: 12, review_minutes: 5, hourly_cost_inr: 600, target_stp: 0.7 });
  const [imp, setImp] = useState<Impact | null>(null);
  useEffect(() => {
    const q = new URLSearchParams(Object.entries(a).map(([k, v]) => [k, String(v)])).toString();
    const load = () => api<Impact>(`/impact?${q}`).then(setImp).catch(() => {});
    load(); const t = setInterval(load, 4000); return () => clearInterval(t);
  }, [a]);
  const field = (k: keyof typeof a, label: string, step = 1) => (
    <label className="flex items-center justify-between gap-2 text-xs"><span className="text-muted-foreground">{label}</span>
      <input type="number" step={step} value={a[k]} onChange={(e) => setA({ ...a, [k]: Number(e.target.value) })} className="w-20 rounded border border-border px-2 py-1 text-right tabular-nums" /></label>
  );
  const t = imp?.scenario_target;
  return (
    <Card>
      <CardHeader><CardTitle className="flex items-center gap-2"><IndianRupee className="h-4 w-4" />Business impact</CardTitle>
        <CardDescription>What this process is worth to an AP team. Every number below comes from the inputs on the right; change them to the customer’s own baseline.</CardDescription></CardHeader>
      <CardContent className="grid gap-6 md:grid-cols-[1fr_240px]">
        <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
          <div><p className="text-xs text-muted-foreground">AP hours saved / month</p><p className="text-2xl font-semibold tabular-nums">{t ? Math.round(t.hours_saved) : "–"}</p><p className="text-xs text-muted-foreground">{t ? `${t.hours_before} → ${t.hours_after} h · ${t.fte_freed} FTE` : ""}</p></div>
          <div><p className="text-xs text-muted-foreground">Cost per invoice</p><p className="text-2xl font-semibold tabular-nums">{t ? `₹${t.cost_per_invoice_after_inr}` : "–"}</p><p className="text-xs text-muted-foreground">{t ? `was ₹${t.cost_per_invoice_before_inr} manual` : ""}</p></div>
          <div><p className="text-xs text-muted-foreground">Saving / month</p><p className="text-2xl font-semibold tabular-nums">{t ? inr(t.monthly_saving_inr) : "–"}</p><p className="text-xs text-muted-foreground">at {pct(a.target_stp)} straight-through</p></div>
          <div><p className="text-xs text-muted-foreground">Money stopped (this demo)</p><p className="text-2xl font-semibold tabular-nums">{imp ? inr(imp.observed.money_stopped_inr) : "–"}</p><p className="text-xs text-muted-foreground">{imp ? `${imp.observed.invoices_stopped} duplicate / fraud / blocked · +${inr(imp.observed.overbilling_caught_inr)} over-billing` : ""}</p></div>
          <p className="col-span-2 text-xs text-muted-foreground md:col-span-4">This demo set is exception-heavy on purpose ({imp ? pct(imp.observed.straight_through_rate) : "–"} straight-through here). Benchmark: Ardent Partners 2024 puts average AP cost at USD 9.40 per invoice, best-in-class USD 2.78.</p>
        </div>
        <div className="space-y-2 rounded-md border border-border p-3">
          <p className="text-xs font-medium">Assumptions</p>
          {field("monthly_invoices", "Invoices / month", 100)}
          {field("manual_minutes", "Manual minutes / invoice")}
          {field("review_minutes", "Review minutes / exception")}
          {field("hourly_cost_inr", "Loaded cost ₹ / hour", 50)}
          {field("target_stp", "Target straight-through", 0.05)}
        </div>
      </CardContent>
    </Card>
  );
}

export default function Dashboard() {
  const [runs, setRuns] = useState<Run[]>([]);
  const [stats, setStats] = useState<Stats | null>(null);
  const [filter, setFilter] = useState<string>("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const [samples, setSamples] = useState<Sample[]>([]);
  useEffect(() => { api<Sample[]>("/samples").then(setSamples).catch(() => {}); }, []);

  async function runSample(name: string) {
    setBusy(true);
    try {
      const body = await api<{ results: { run_id: string }[] }>(`/demo/sample/${encodeURIComponent(name)}`, { method: "POST" });
      const first = body.results?.[0];
      if (first?.run_id) window.location.href = runHref(first.run_id);
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }

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
    if (first?.run_id) window.location.href = runHref(first.run_id);
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">Run history</h1>
          <p className="text-sm text-muted-foreground">Drop in an invoice (PDF, scanned PDF, CSV or JSON). Each one is read, matched to its PO and goods receipt, checked for duplicates, fraud and GST errors, then approved, held for one human question, or rejected.</p>
        </div>
        <div className="flex gap-2">
          <input ref={fileRef} type="file" accept=".json,.csv,.pdf,.txt" className="hidden" onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])} />
          <Button variant="outline" onClick={() => fileRef.current?.click()}><Upload className="h-4 w-4" />Upload invoice</Button>
          <Button variant="outline" onClick={load} title="Refresh"><RefreshCw className="h-4 w-4" />Refresh</Button>
          <Button onClick={seed} disabled={busy}><Play className="h-4 w-4" />{busy ? "Seeding…" : "Run demo seed"}</Button>
        </div>
      </div>
      {samples.length > 0 && (
        <Card>
          <CardHeader className="pb-2"><CardTitle className="text-sm">Try a real input</CardTitle><CardDescription>Runs the file through exactly the same path as an upload. Download it to see the original.</CardDescription></CardHeader>
          <CardContent className="flex flex-wrap gap-2">
            {samples.map((s) => (
              <div key={s.name} className="flex items-center gap-1 rounded-md border border-border bg-white px-2 py-1">
                <button className="flex cursor-pointer items-center gap-1 text-left text-xs hover:underline disabled:opacity-50" disabled={busy} onClick={() => runSample(s.name)} title={s.description}>
                  <FileText className="h-3.5 w-3.5 shrink-0" /><span className="font-mono">{(s as any).label || s.name}</span><span className="hidden text-muted-foreground lg:inline">· {s.description}</span>
                </button>
                <a href={`${API}/samples/${encodeURIComponent(s.name)}`} className="text-muted-foreground hover:text-foreground" title="Download the original file"><Download className="h-3.5 w-3.5" /></a>
              </div>
            ))}
          </CardContent>
        </Card>
      )}

      {error && <div className="rounded-md border border-rose-200 bg-rose-50 p-3 text-sm text-rose-800">{error}</div>}

      <div className="grid grid-cols-2 gap-4 md:grid-cols-5">
        <Kpi label="Invoices processed" value={`${stats?.completed ?? 0}/${stats?.invoices ?? 0}`} />
        <Kpi label="Auto-approved" value={pct(stats?.straight_through_rate)} />
        <Kpi label="Pending review" value={`${stats?.pending_review ?? 0}`} hint="HOLD_FOR_REVIEW without a human decision" />
        <Kpi label="Accuracy vs labels" value={pct(stats?.accuracy_vs_labels)} hint={`${stats?.labelled ?? 0} labelled seed invoices`} />
        <Kpi label="False approvals" value={`${stats?.false_approvals ?? 0}`} hint="Bad invoices auto-approved" />
      </div>

      <ImpactCard />

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
                <TR key={r.id} className="cursor-pointer" onClick={() => (window.location.href = runHref(r.id))}>
                  <TD className="whitespace-nowrap text-xs text-muted-foreground">{isNaN(Date.parse(r.invoice.received_at)) ? r.invoice.received_at : new Date(r.invoice.received_at).toLocaleString("en-IN", { dateStyle: "medium", timeStyle: "short" })}</TD>
                  <TD className="max-w-48 truncate">{r.invoice.vendor_name}</TD>
                  <TD className="whitespace-nowrap font-mono text-xs"><Link href={runHref(r.id)}>{r.invoice.invoice_number}</Link></TD>
                  <TD className="whitespace-nowrap font-mono text-xs">{r.matched_po || r.invoice.po_number || "–"}</TD>
                  <TD className="whitespace-nowrap text-right tabular-nums">{r.invoice.currency} {Number(r.invoice.total ?? 0).toLocaleString()}</TD>
                  <TD><DecisionBadge decision={r.status === "completed" ? r.effective_decision : null} /></TD>
                  <TD className="tabular-nums">{r.confidence != null ? `${Math.round(r.confidence * 100)}%` : "–"}</TD>
                  <TD className="font-mono text-xs">{r.primary_reason || "–"}</TD>
                  <TD>{r.review_status === "pending" ? <Badge variant="hold">pending</Badge> : r.review_status === "resolved" ? <Badge variant="outline">resolved</Badge> : <span className="text-xs text-muted-foreground">–</span>}</TD>
                  <TD className="text-xs text-muted-foreground">{r.invoice.expected ? (r.invoice.expected.decision === r.decision ? "✓ " : "✗ ") + (r.invoice.expected.decision === "HOLD_FOR_REVIEW" ? "HOLD" : r.invoice.expected.decision) : "–"}</TD>
                </TR>
              ))}
              {runs.length === 0 && <TR><TD colSpan={10} className="py-10 text-center text-muted-foreground">No runs yet. Click “Run demo seed”, try a sample PDF above, or upload an invoice.</TD></TR>}
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
