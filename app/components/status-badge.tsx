type StatusBadgeProps = {
  value: string | null | undefined;
};

const toneMap: Record<string, "success" | "warning" | "danger" | "neutral"> = {
  accepted: "success",
  active: "success",
  succeeded: "success",
  complete: "success",
  ready: "success",
  warning: "warning",
  partial_failure: "warning",
  queued: "warning",
  running: "warning",
  pending: "warning",
  failed: "danger",
  provider_rejected: "danger",
  rejected: "danger",
  error: "danger",
};

export function StatusBadge({ value }: StatusBadgeProps) {
  const normalized = (value ?? "unknown").toLowerCase();
  const tone = toneMap[normalized] ?? "neutral";

  return (
    <span className="badge" data-tone={tone}>
      {normalized.replaceAll("_", " ")}
    </span>
  );
}
