"use client";

import { useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { browserApiPath } from "@/app/lib/browser-api";
import type { ClientConfig } from "@/app/lib/types";

export function CreateMovieForm({ config }: { config: ClientConfig }) {
  const router = useRouter();
  const [title, setTitle] = useState("");
  const [files, setFiles] = useState<FileList | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isPending, startTransition] = useTransition();

  // Server-supplied so the two copies of this rule cannot drift. The old
  // hardcoded regex accepted names the API rejected (a-b-c-d.mp4, a-b-c.exe),
  // which failed only after the movie had already been created.
  const filenamePattern = new RegExp(config.filenamePattern);

  function validate(selected: File[]) {
    if (selected.length < config.minClipCount) {
      throw new Error(`Choose at least ${config.minClipCount} clips.`);
    }
    if (selected.length > config.maxClipCount) {
      throw new Error(`Choose no more than ${config.maxClipCount} clips.`);
    }
    for (const file of selected) {
      if (!filenamePattern.test(file.name)) {
        throw new Error(`${file.name} must use team-player-tier.fileext.`);
      }
      const extension = file.name.split(".").pop()?.toLowerCase() ?? "";
      if (!config.supportedExtensions.includes(extension)) {
        throw new Error(
          `${file.name} uses .${extension}. Supported: ${config.supportedExtensions.join(", ")}.`,
        );
      }
      if (file.size > config.maxClipSizeBytes) {
        throw new Error(`${file.name} is larger than the upload limit.`);
      }
    }
  }

  function submit() {
    startTransition(async () => {
      setError(null);

      try {
        const selected = Array.from(files ?? []);
        validate(selected);

        // Upload first, create the movie second. The old order created the
        // movie up front, so any rejected filename left an empty movie behind
        // with no way to delete it. Clips that arrive without a movie simply
        // join the reusable clip library.
        const formData = new FormData();
        selected.forEach((file) => formData.append("files", file));

        const uploadResponse = await fetch(browserApiPath("/clips"), {
          method: "POST",
          body: formData,
        });
        if (!uploadResponse.ok) {
          throw new Error(await uploadResponse.text());
        }
        const clips = (await uploadResponse.json()) as { id: string }[];

        const createResponse = await fetch(browserApiPath("/movies"), {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ title }),
        });
        if (!createResponse.ok) {
          throw new Error(await createResponse.text());
        }
        const movie = (await createResponse.json()) as { id: string };

        const timelineResponse = await fetch(
          browserApiPath(`/movies/${movie.id}/timeline`),
          {
            method: "PUT",
            headers: { "content-type": "application/json" },
            body: JSON.stringify({
              slots: clips.map((clip, index) => ({
                id: null,
                sort_index: index,
                clip_id: clip.id,
                overlay_text: "",
              })),
            }),
          },
        );
        if (!timelineResponse.ok) {
          throw new Error(await timelineResponse.text());
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
          <label htmlFor="movie-files">
            Clips (at least {config.minClipCount})
          </label>
          <input
            accept={config.supportedExtensions.map((ext) => `.${ext}`).join(",")}
            id="movie-files"
            multiple
            onChange={(event) => setFiles(event.target.files)}
            type="file"
          />
          <p className="text-soft">
            Named team-player-tier, up to{" "}
            {config.maxClipDurationSeconds.toFixed(1)}s each.
          </p>
        </div>
      </div>

      {error ? (
        <p className="badge" data-tone="danger">
          {error}
        </p>
      ) : null}

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
