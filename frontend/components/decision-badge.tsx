import { Badge } from "@/components/ui/badge";

export function DecisionBadge({ decision }: { decision: string | null | undefined }) {
  if (!decision) return <Badge variant="muted">processing</Badge>;
  const v = decision === "APPROVE" ? "approve" : decision === "REJECT" ? "reject" : "hold";
  const label = decision === "HOLD_FOR_REVIEW" ? "HOLD" : decision;
  return <Badge variant={v}>{label}</Badge>;
}
