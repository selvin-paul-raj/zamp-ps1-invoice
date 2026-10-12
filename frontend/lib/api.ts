// Bundled build: same origin under /api. Dev server: a separate API (default localhost:8000).
export const API = process.env.NEXT_PUBLIC_API_URL || (process.env.NODE_ENV === "development" ? "http://localhost:8000" : "/api");
export const runHref = (id: string) => `/run/?id=${id}`;

export type Handoff = { code: string; question: string; owner: string; next_action: string; vendor_email_draft: string | null } | null;
export type Impact = {
  assumptions: { monthly_invoices: number; manual_minutes_per_invoice: number; review_minutes_per_exception: number; loaded_hourly_cost_inr: number; target_stp: number; notes: string[] };
  observed: { invoices: number; straight_through_rate: number; money_stopped_inr: number; invoices_stopped: number; overbilling_caught_inr: number };
  scenario_observed: Scenario; scenario_target: Scenario;
};
export type Scenario = { straight_through_rate: number; hours_before: number; hours_after: number; hours_saved: number; fte_freed: number; cost_per_invoice_before_inr: number; cost_per_invoice_after_inr: number; monthly_saving_inr: number };
export type Sample = { name: string; description: string; kind: string };

export type Finding = { code: string; severity: "info" | "hold" | "reject"; message: string; step: string; certainty: number; evidence: Record<string, unknown> };
export type EvidenceRec = {
  rule_id: string; source_system: string; source_name: string; record_id: string | null; field: string; expected: unknown; actual: unknown;
  result: "match" | "mismatch" | "missing" | "info"; step: string; source_as_of: string | null; doc_ref: string | null; note: string; finding_code: string | null; read_at: string;
};
export type ModelCall = {
  role: string; provider: string; mode: string; model: string | null; question_type: string; question: string; options: unknown; answer: unknown;
  probability: number | null; confidence: number | null; threshold: number | null; outcome: string; latency_ms: number; input_tokens: number; cost_usd: number; error?: string | null;
};
export type RunEvent = { seq: number; step: string; status: string; title: string; message: string; data: any; attempt: number; ts: string };
export type Run = {
  id: string; invoice_id: string; status: string; attempt: number; decision: string | null; final_decision: string | null;
  effective_decision: string | null; confidence: number | null; primary_reason: string | null; reasons: Finding[]; explanation: string | null;
  review_status: string; vendor_id: string | null; matched_po: string | null; created_at: string; finished_at: string | null; duration_ms: number | null;
  invoice: { invoice_number: string; vendor_name: string; po_number: string | null; currency: string; total: string | null; source: string; received_at: string;
    expected?: { decision: string; reason: string | null; story?: string; seed_id?: string } | null; payload?: any };
  handoff?: Handoff;
  evidence?: EvidenceRec[]; model_calls?: ModelCall[];
  evidence_summary?: { records: number; sources: string[]; mismatches: number; model_calls: number; model_cost_usd: number };
  events?: RunEvent[]; reviews?: { action: string; final_decision: string; reviewer: string; note: string; created_at: string }[];
};
export type Stats = {
  invoices: number; runs: number; completed: number; decisions: Record<string, number>; top_reasons: [string, number][];
  pending_review: number; straight_through_rate: number | null; labelled: number; accuracy_vs_labels: number | null; false_approvals: number; p50_ms: number | null;
};

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  // Optional API key (only when the server runs AUTH_MODE=apikey): localStorage.setItem("apiKey", "...")
  const key = typeof window !== "undefined" ? window.localStorage.getItem("apiKey") : null;
  const res = await fetch(`${API}${path}`, { cache: "no-store", ...init, headers: { "Content-Type": "application/json", ...(key ? { "X-API-Key": key } : {}), ...(init?.headers || {}) } });
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
  return res.json();
}
