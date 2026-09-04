import Link from "next/link";
import { DeleteButton } from "@/app/components/delete-button";
import { PageHeader } from "@/app/components/page-header";
import { StatusBadge } from "@/app/components/status-badge";
import { SummaryCard } from "@/app/components/summary-card";
import { getMovies } from "@/app/lib/api";

export default async function MoviesPage() {
  const movies = await getMovies();
  const activeMovies = movies.filter((movie) => movie.status !== "archived");
  const videosReady = movies.filter((movie) => movie.latestVideo).length;

  return (
    <main className="page-shell page-grid">
      <PageHeader
        actions={
          <Link className="button button-primary" href="/movies/new">
            New Movie
          </Link>
        }
        description="Track current clip assemblies, render status, and the latest generated output for every movie."
        eyebrow="Movies"
        title="Clip Assembly Queue"
      />

      <section className="stats-grid">
        <SummaryCard
          detail="Movies currently managed by the authenticated user."
          label="Total Movies"
          value={movies.length}
        />
        <SummaryCard
          detail="Movies that still have active editing or render work."
          label="Active Movies"
          value={activeMovies.length}
        />
        <SummaryCard
          detail="Movies with at least one rendered output stored."
          label="Videos Ready"
          value={videosReady}
        />
      </section>

      <section className="table-card">
        {movies.length === 0 ? (
          <div className="empty-state">
            No movies yet. <Link href="/movies/new">Create your first movie</Link> by
            uploading clips named team-player-tier.
          </div>
        ) : (
        <table>
          <thead>
            <tr>
              <th scope="col">Movie</th>
              <th scope="col">Status</th>
              <th scope="col">Slots</th>
              <th scope="col">Latest Output</th>
              <th scope="col">Updated</th>
              <th scope="col"><span className="sr-only">Actions</span></th>
            </tr>
          </thead>
          <tbody>
            {movies.map((movie) => (
              <tr key={movie.id}>
                <td>
                  <Link href={`/movies/${movie.id}/edit`}>
                    <strong>{movie.title}</strong>
                  </Link>
                </td>
                <td>
                  <StatusBadge value={movie.status} />
                </td>
                <td>{movie.slotCount}</td>
                <td>
                  {movie.latestVideo?.downloadUrl ? (
                    <div className="stack-sm">
                      <video controls preload="metadata" src={movie.latestVideo.downloadUrl} />
                      <a href={movie.latestVideo.downloadUrl} target="_blank" rel="noreferrer">
                        Download MP4
                      </a>
                    </div>
                  ) : "No render yet"}
                </td>
                <td>{new Date(movie.updatedAt).toLocaleString()}</td>
                <td>
                  <DeleteButton
                    label={`Delete ${movie.title}`}
                    path={`/movies/${movie.id}`}
                  />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        )}
      </section>
    </main>
  );
}
