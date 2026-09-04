export default function Loading() {
  return (
    <main className="page-shell page-grid" aria-busy="true">
      <div className="panel stack-sm">
        <span className="eyebrow">Loading</span>
        <p className="text-soft">Fetching the latest from the studio backend.</p>
      </div>
    </main>
  );
}
