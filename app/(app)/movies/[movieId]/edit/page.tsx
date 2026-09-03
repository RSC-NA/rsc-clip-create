import { PageHeader } from "@/app/components/page-header";
import { TimelineEditor } from "@/app/components/timeline-editor";
import { getMovie } from "@/app/lib/api";

export default async function EditMoviePage({
  params,
}: {
  params: Promise<{ movieId: string }>;
}) {
  const { movieId } = await params;
  const movie = await getMovie(movieId);

  return (
    <main className="page-shell page-grid">
      <PageHeader
        description="Place clips in order, add or remove positions in the sequence, and annotate each slot with on-screen text."
        eyebrow="Editor"
        title={movie.title}
      />

      <TimelineEditor
        availableClips={movie.clips}
        initialJob={null}
        initialSlots={movie.slots}
        movieId={movie.id}
      />
    </main>
  );
}
