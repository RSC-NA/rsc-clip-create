export function SummaryCard({
  label,
  value,
  detail,
}: {
  label: string;
  value: string | number;
  detail: string;
}) {
  return (
    <article className="stat-card">
      <span className="mono-label">{label}</span>
      <strong>{value}</strong>
      <p className="text-soft">{detail}</p>
    </article>
  );
}
