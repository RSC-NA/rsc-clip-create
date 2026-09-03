import { PageHeader } from "@/app/components/page-header";
import { StatusBadge } from "@/app/components/status-badge";
import { getAdminErrors } from "@/app/lib/api";
import { requireSession } from "@/app/lib/session";

export default async function AdminErrorsPage() {
  await requireSession({ admin: true });
  const errors = await getAdminErrors();

  return (
    <main className="page-shell page-grid">
      <PageHeader
        description="This page exposes both job-level and clip-level render failures, including provider categories and the reason individual clips were rejected."
        eyebrow="Administrator"
        title="Render Diagnostics"
      />

      <div className="detail-list">
        {errors.map((error) => (
          <article key={`${error.jobId}-${error.attemptNumber}`}>
            <div className="row-between">
              <div className="stack-sm">
                <span className="mono-label">
                  {error.movieTitle} / Attempt {error.attemptNumber}
                </span>
                <strong>{error.errorSummary ?? "Render attempt failed."}</strong>
                <p className="text-soft">
                  Owner: {error.ownerName} • Stage: {error.failureStage ?? "unknown"} •
                  Category: {error.errorCategory ?? "unknown"}
                </p>
              </div>
              <StatusBadge value={error.status} />
            </div>

            <div className="stack-sm">
              {error.clipResults.map((clip) => (
                <div className="row-between" key={clip.id}>
                  <div>
                    <strong>{clip.clipName}</strong>
                    <p className="text-soft">
                      {clip.reasonSummary ?? "No clip-specific reason recorded."}
                    </p>
                    {clip.technicalDetail ? (
                      <p className="text-soft">{clip.technicalDetail}</p>
                    ) : null}
                  </div>
                  <StatusBadge value={clip.status} />
                </div>
              ))}
            </div>
          </article>
        ))}
      </div>
    </main>
  );
}
