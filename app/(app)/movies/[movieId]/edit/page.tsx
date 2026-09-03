import { PageHeader } from "@/app/components/page-header";
import { TimelineEditor } from "@/app/components/timeline-editor";
import { getLogos, getMovie } from "@/app/lib/api";

export default async function EditMoviePage({
  params,
}: {
  params: Promise<{ movieId: string }>;
}) {
  const { movieId } = await params;
  const [movie, logos] = await Promise.all([getMovie(movieId), getLogos()]);

  return (
    <main className="page-shell page-grid">
      <PageHeader
        description="Place clips in order, add or remove positions in the sequence, and choose how each cut transitions."
        eyebrow="Editor"
        title={movie.title}
      />

      <TimelineEditor
        availableClips={movie.clips}
        initialJob={null}
        initialSlots={movie.slots}
        logoTeams={logos.map((logo) => logo.team.trim().toLowerCase())}
        movieId={movie.id}
        transitionHoldSeconds={movie.transitionHoldSeconds}
        transitionStyle={movie.transitionStyle}
        transitionStyles={movie.transitionStyles}
      />
    </main>
  );
}
