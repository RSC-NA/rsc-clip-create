import { PageHeader } from "@/app/components/page-header";
import { StatusBadge } from "@/app/components/status-badge";
import { getAdminVideos } from "@/app/lib/api";
import { requireSession } from "@/app/lib/session";

export default async function AdminVideosPage() {
  await requireSession({ admin: true });
  const videos = await getAdminVideos();

  return (
    <main className="page-shell page-grid">
      <PageHeader
        description="Inspect every generated output, its owner, and the latest render attempt state that produced or blocked it."
        eyebrow="Administrator"
        title="Video Inventory"
      />

      <section className="table-card">
        {videos.length === 0 ? (
          <div className="empty-state">No videos have been rendered yet.</div>
        ) : (
        <table>
          <thead>
            <tr>
              <th scope="col">Movie</th>
              <th scope="col">Owner</th>
              <th scope="col">Status</th>
              <th scope="col">Latest Attempt</th>
              <th scope="col">Location</th>
              <th scope="col">Created</th>
            </tr>
          </thead>
          <tbody>
            {videos.map((video) => (
              <tr key={video.id}>
                <td>
                  <strong>{video.movieTitle}</strong>
                </td>
                <td>{video.ownerName}</td>
                <td>
                  <StatusBadge value={video.status} />
                </td>
                <td>
                  <StatusBadge value={video.latestAttemptStatus ?? "unknown"} />
                </td>
                <td>{video.storagePath}</td>
                <td>{new Date(video.createdAt).toLocaleString()}</td>
              </tr>
            ))}
          </tbody>
        </table>
        )}
      </section>
    </main>
  );
}
