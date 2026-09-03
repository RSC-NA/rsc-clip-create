import { PageHeader } from "@/app/components/page-header";
import { StatusBadge } from "@/app/components/status-badge";
import { getAdminUsers } from "@/app/lib/api";
import { requireSession } from "@/app/lib/session";

export default async function AdminUsersPage() {
  await requireSession({ admin: true });
  const users = await getAdminUsers();

  return (
    <main className="page-shell page-grid">
      <PageHeader
        description="Review who has access to the system, how active they are, and whether they are designated as an administrator."
        eyebrow="Administrator"
        title="User Management"
      />

      <section className="table-card">
        <table>
          <thead>
            <tr>
              <th>Name</th>
              <th>Discord</th>
              <th>Admin</th>
              <th>Movies</th>
              <th>Videos</th>
              <th>Last Sign In</th>
            </tr>
          </thead>
          <tbody>
            {users.map((user) => (
              <tr key={user.id}>
                <td>
                  <strong>{user.displayName}</strong>
                  <div className="text-soft">{user.email ?? "No email"}</div>
                </td>
                <td>{user.discordUsername ?? "Unknown"}</td>
                <td>
                  <StatusBadge value={user.isAdmin ? "active" : "pending"} />
                </td>
                <td>{user.movieCount}</td>
                <td>{user.videoCount}</td>
                <td>
                  {user.latestSignInAt
                    ? new Date(user.latestSignInAt).toLocaleString()
                    : "Never"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </main>
  );
}
