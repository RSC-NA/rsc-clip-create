import { PageHeader } from "@/app/components/page-header";
import { TimelineEditor } from "@/app/components/timeline-editor";
import { getClientConfig, getMovie } from "@/app/lib/api";

export default async function EditMoviePage({
  params,
}: {
  params: Promise<{ movieId: string }>;
}) {
  const { movieId } = await params;
  const [movie, config] = await Promise.all([
    getMovie(movieId),
    getClientConfig(),
  ]);

  return (
    <main className="page-shell page-grid">
      <PageHeader
        description="Place clips in order, and add or remove positions in the sequence. Every cut is covered by the brand sweep."
        eyebrow="Editor"
        title={movie.title}
      />

      <TimelineEditor
        availableClips={movie.clips}
        initialJob={movie.latestJob}
        initialSlots={movie.slots}
        minClipCount={config.minClipCount}
        movieId={movie.id}
      />
    </main>
  );
}
