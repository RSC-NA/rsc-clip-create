import { CreateMovieForm } from "@/app/components/create-movie-form";
import { PageHeader } from "@/app/components/page-header";

export default function NewMoviePage() {
  return (
    <main className="page-shell page-grid">
      <PageHeader
        description="Upload at least two clips named team-player-tier.ext. They will be labeled, normalized, and combined in upload order."
        eyebrow="Create"
        title="Start a New Movie"
      />

      <div className="split">
        <CreateMovieForm />

        <section className="panel stack-md">
          <div className="stack-sm">
            <span className="mono-label">What happens next</span>
            <p className="text-soft">
              Every new movie starts with the uploaded clips in their selected order.
            </p>
          </div>
          <div className="detail-list">
            <article>
              <strong>1. Upload clips</strong>
              <p className="text-soft">
                Filenames are validated and their team, player, and tier metadata is stored.
              </p>
            </article>
            <article>
              <strong>2. Build the sequence</strong>
              <p className="text-soft">
                Review the order and the metadata that will appear in the lower-left label.
              </p>
            </article>
            <article>
              <strong>3. Queue Veo</strong>
              <p className="text-soft">
                FFmpeg normalizes, labels, concatenates, and stores a downloadable MP4.
              </p>
            </article>
          </div>
        </section>
      </div>
    </main>
  );
}
