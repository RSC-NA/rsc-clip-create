import Link from "next/link";
import { PageHeader } from "@/app/components/page-header";
import { SummaryCard } from "@/app/components/summary-card";
import { requireSession } from "@/app/lib/session";
import { getAdminDashboard } from "@/app/lib/api";

export default async function AdminDashboardPage() {
  await requireSession({ admin: true });
  const dashboard = await getAdminDashboard();

  return (
    <main className="page-shell page-grid">
      <PageHeader
        actions={
          <div className="inline-actions">
            <Link className="button button-secondary" href="/admin/users">
              Users
            </Link>
            <Link className="button button-secondary" href="/admin/errors">
              Errors
            </Link>
          </div>
        }
        description="Administrator-only management pages track people, outputs, and the exact clip/provider failures that blocked any render."
        eyebrow="Administrator"
        title="Operations Console"
      />

      <section className="stats-grid">
        <SummaryCard
          detail="Accounts currently recognized by the backend."
          label="Users"
          value={dashboard.totalUsers}
        />
        <SummaryCard
          detail="Movie records created across all users."
          label="Movies"
          value={dashboard.totalMovies}
        />
        <SummaryCard
          detail="Stored output videos with known locations."
          label="Videos"
          value={dashboard.totalVideos}
        />
        <SummaryCard
          detail="Render attempts with failed or partially failed status."
          label="Failed Attempts"
          value={dashboard.failedAttempts}
        />
      </section>
    </main>
  );
}
