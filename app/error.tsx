"use client";

export default function RootError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <main className="auth-shell">
      <section className="auth-card stack-lg">
        <div className="stack-sm">
          <p className="eyebrow">Something went wrong</p>
          <h1>This page could not be loaded.</h1>
          <p className="text-soft">
            The studio could not reach part of its backend. This is usually
            temporary.
          </p>
          {error.digest ? (
            <p className="mono-label">Reference: {error.digest}</p>
          ) : null}
        </div>
        <div className="hero-actions">
          <button className="button button-primary" onClick={reset} type="button">
            Try Again
          </button>
          <a className="button button-secondary" href="/movies">
            Back to Movies
          </a>
        </div>
      </section>
    </main>
  );
}
