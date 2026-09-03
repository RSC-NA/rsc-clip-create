"use client";

import { useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { browserApiPath } from "@/app/lib/browser-api";

export function CreateMovieForm() {
  const router = useRouter();
  const [title, setTitle] = useState("");
  const [files, setFiles] = useState<FileList | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isPending, startTransition] = useTransition();

  function submit() {
    startTransition(async () => {
      setError(null);

      try {
        if (!files || files.length < 2) {
          throw new Error("Choose at least two clips.");
        }
        const invalid = Array.from(files).find(
          (file) => !/^[^-\.]+-[^-\.]+-[^.]+\.[A-Za-z0-9]+$/.test(file.name),
        );
        if (invalid) {
          throw new Error(`${invalid.name} must use team-player-tier.fileext.`);
        }
        const createResponse = await fetch(browserApiPath("/movies"), {
          method: "POST",
          headers: {
            "content-type": "application/json",
          },
          body: JSON.stringify({ title }),
        });

        if (!createResponse.ok) {
          throw new Error(await createResponse.text());
        }

        const movie = (await createResponse.json()) as { id: string };

        if (files && files.length > 0) {
          const formData = new FormData();

          Array.from(files).forEach((file) => {
            formData.append("files", file);
          });

          const uploadResponse = await fetch(
            browserApiPath(`/clips?movie_id=${movie.id}`),
            {
              method: "POST",
              body: formData,
            },
          );

          if (!uploadResponse.ok) {
            throw new Error(await uploadResponse.text());
          }
        }

        router.push(`/movies/${movie.id}/edit`);
        router.refresh();
      } catch (caught) {
        setError(
          caught instanceof Error ? caught.message : "Failed to create the movie.",
        );
      }
    });
  }

  return (
    <section className="panel stack-md">
      <div className="form-grid">
        <div className="field">
          <label htmlFor="movie-title">Movie Title</label>
          <input
            id="movie-title"
            onChange={(event) => setTitle(event.target.value)}
            placeholder="Spring Product Launch Long Cut"
            value={title}
          />
        </div>

        <div className="field">
          <label htmlFor="movie-files">Clips (at least 2)</label>
          <input
            id="movie-files"
            multiple
            onChange={(event) => setFiles(event.target.files)}
            type="file"
          />
        </div>
      </div>

      {error ? <p className="badge" data-tone="danger">{error}</p> : null}

      <div className="inline-actions">
        <button
          className="button button-primary"
          disabled={isPending || title.trim().length === 0}
          onClick={submit}
          type="button"
        >
          {isPending ? "Creating..." : "Create Movie"}
        </button>
      </div>
    </section>
  );
}
