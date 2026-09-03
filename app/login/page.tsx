import Link from "next/link";
import { redirect } from "next/navigation";
import { getSession } from "@/app/lib/api";

export default async function LoginPage() {
  if (await getSession()) {
    redirect("/movies");
  }

  return (
    <main className="auth-shell">
      <section className="auth-card stack-lg">
        <div className="stack-sm">
          <p className="eyebrow">Authentication</p>
          <h1>Sign in with Discord to manage clip-based renders.</h1>
          <p className="text-soft">
            Session state is owned by the FastAPI backend so the editor, worker,
            and administrator pages all share the same authorization model.
          </p>
        </div>

        <div className="cards-grid">
          <article className="panel muted-panel">
            <span className="mono-label">Creator Access</span>
            <p className="text-soft">
              Build movies, upload source clips, manage timelines, and queue new
              renders.
            </p>
          </article>
          <article className="panel muted-panel">
            <span className="mono-label">Admin Access</span>
            <p className="text-soft">
              Review users, inspect failed render attempts, and diagnose clip-level
              errors across the system.
            </p>
          </article>
        </div>

        <div className="hero-actions">
          <Link
            className="button button-primary"
            href="/api/backend/auth/discord/login?next=/movies"
          >
            Continue With Discord
          </Link>
          <Link className="button button-secondary" href="/">
            Back Home
          </Link>
        </div>
      </section>
    </main>
  );
}
