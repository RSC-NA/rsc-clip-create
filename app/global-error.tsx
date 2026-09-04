"use client";

/** Last-resort boundary: replaces the root layout, so it ships its own html/body. */
export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <html lang="en">
      <body
        style={{
          fontFamily: "system-ui, sans-serif",
          margin: 0,
          minHeight: "100vh",
          display: "grid",
          placeItems: "center",
          background: "#f3efe7",
          color: "#211d16",
        }}
      >
        <main style={{ maxWidth: "34rem", padding: "2rem", textAlign: "center" }}>
          <h1 style={{ fontSize: "1.5rem" }}>Clip Create Studio could not start.</h1>
          <p style={{ color: "#62584a" }}>
            An unexpected error occurred while rendering the application.
          </p>
          {error.digest ? (
            <p style={{ fontFamily: "monospace", fontSize: ".8rem" }}>
              Reference: {error.digest}
            </p>
          ) : null}
          <button
            onClick={reset}
            style={{
              marginTop: "1rem",
              padding: ".6rem 1.2rem",
              borderRadius: "999px",
              border: "1px solid #92401f",
              background: "#c65a32",
              color: "white",
              cursor: "pointer",
            }}
            type="button"
          >
            Try Again
          </button>
        </main>
      </body>
    </html>
  );
}
