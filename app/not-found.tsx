import Link from "next/link";

export default function NotFound() {
  return (
    <main className="auth-shell">
      <section className="auth-card stack-lg">
        <div className="stack-sm">
          <p className="eyebrow">Not found</p>
          <h1>That page does not exist.</h1>
          <p className="text-soft">
            The link may be out of date, or the item may have been deleted.
          </p>
        </div>
        <div className="hero-actions">
          <Link className="button button-primary" href="/movies">
            Back to Movies
          </Link>
        </div>
      </section>
    </main>
  );
}
