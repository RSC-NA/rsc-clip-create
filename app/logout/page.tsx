import { logoutAction } from "@/app/actions/auth";

export default function LogoutPage() {
  return (
    <main className="auth-shell">
      <section className="auth-card stack-lg">
        <div className="stack-sm">
          <p className="eyebrow">Session</p>
          <h1>End the current studio session.</h1>
          <p className="text-soft">
            Logging out clears the backend-owned cookie and returns you to the
            Discord sign-in page.
          </p>
        </div>

        <form action={logoutAction}>
          <button className="button button-primary" type="submit">
            Log Out
          </button>
        </form>
      </section>
    </main>
  );
}
