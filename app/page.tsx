import Link from "next/link";

const highlights = [
  "Filename-driven clip batches with team, player, and tier labels.",
  "Discord-backed authentication handled by FastAPI sessions.",
  "Admin diagnostics for FFmpeg failures, rejected clips, and render history.",
];

export default function Home() {
  return (
    <main className="hero-shell">
      <section className="hero-card">
        <p className="eyebrow">Next.js + FastAPI Studio</p>
        <h1>Turn named clips into a finished, labeled highlight reel.</h1>
        <p className="hero-copy">
          Upload clips named team-player-tier, review the order, and download one
          normalized MP4 with a readable label burned into every clip.
        </p>
        <div className="hero-actions">
          <Link className="button button-primary" href="/movies">
            Open Studio
          </Link>
          <Link className="button button-secondary" href="/login">
            Sign In
          </Link>
        </div>
      </section>

      <section className="grid gap-4 md:grid-cols-3">
        {highlights.map((item) => (
          <article className="panel muted-panel" key={item}>
            <p>{item}</p>
          </article>
        ))}
      </section>
    </main>
  );
}
