"use client";

import { useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { browserApiPath } from "@/app/lib/browser-api";
import { readApiError } from "@/app/lib/api-error";
import type { ClientConfig } from "@/app/lib/types";

// POST /clips stores each clip as `<uuid4>-<filename>` and rejects it when that
// is over MAX_FILENAME_BYTES (255, backend/app/storage.py). The UUID and its
// hyphen take 37 bytes, leaving 218 UTF-8 bytes for the clip's own name.
const MAX_CLIP_NAME_BYTES = 255 - 37;

// UTF-8 byte length, as Python's str.encode("utf-8") measures it on the API.
const utf8Length = (value: string) =>
  Array.from(value).reduce((total, char) => {
    const codePoint = char.codePointAt(0) ?? 0;
    return total + (codePoint < 0x80 ? 1 : codePoint < 0x800 ? 2 : codePoint < 0x10000 ? 3 : 4);
  }, 0);

const mb = (bytes: number) => Math.round(bytes / 1_000_000);
// 31 reads better than 31.0; 15.6 still needs its decimal.
const seconds = (value: number) =>
  Number.isInteger(value) ? String(value) : value.toFixed(1);

export function CreateMovieForm({ config }: { config: ClientConfig }) {
  const router = useRouter();
  const [title, setTitle] = useState("");
  const [files, setFiles] = useState<FileList | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isPending, startTransition] = useTransition();

  // Server-supplied so the two copies of this rule cannot drift. The old
  // hardcoded regex accepted names the API rejected (a-b-c-d.mp4, a-b-c.exe),
  // which failed only after the movie had already been created. The "u" flag
  // makes segment-length quantifiers count code points, like the parser's
  // Python len(), rather than UTF-16 code units (an emoji would count as 2).
  const filenamePattern = new RegExp(config.filenamePattern, "u");

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
      const nameBytes = utf8Length(file.name);
      if (nameBytes > MAX_CLIP_NAME_BYTES) {
        throw new Error(
          `${file.name} is too long: ${nameBytes} bytes; the limit is ` +
            `${MAX_CLIP_NAME_BYTES} UTF-8 bytes per clip name.`,
        );
      }
      if (file.size > config.maxClipSizeBytes) {
        throw new Error(
          `${file.name} is ${mb(file.size)} MB; the limit is ` +
            `${mb(config.maxClipSizeBytes)} MB per clip.`,
        );
      }
    }
  }

  function submit() {
    startTransition(async () => {
      setError(null);

      try {
        // Count Unicode code points like the API, not UTF-16 code units.
        if (Array.from(title).length > 255) {
          throw new Error("Movie title must be 255 characters or fewer.");
        }
        const selected = Array.from(files ?? []);
        validate(selected);

        // Upload first, create the movie second. The old order created the
        // movie up front, so any rejected filename left an empty movie behind
        // with no way to delete it. Clips that arrive without a movie simply
        // join the reusable clip library.
        const formData = new FormData();
        selected.forEach((file) => formData.append("files", file));
        // Measure the encoded request, not just the files: multipart boundaries
        // and part headers count toward the proxy and API's request limit.
        // Clone so measuring the body does not consume the request we send.
        const uploadRequest = new Request(browserApiPath("/clips"), {
          method: "POST",
          body: formData,
        });
        const uploadBytes = (await uploadRequest.clone().blob()).size;
        if (uploadBytes > config.maxUploadBytes) {
          throw new Error(
            `Those ${selected.length} clips require ${mb(uploadBytes)} MB; the limit is ` +
              `${mb(config.maxUploadBytes)} MB per upload. Add them in smaller batches.`,
          );
        }

        const uploadResponse = await fetch(uploadRequest);
        if (!uploadResponse.ok) {
          throw await readApiError(uploadResponse);
        }
        const clips = (await uploadResponse.json()) as { id: string }[];

        const createResponse = await fetch(browserApiPath("/movies"), {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ title }),
        });
        if (!createResponse.ok) {
          throw await readApiError(createResponse);
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
          const timelineError = await readApiError(timelineResponse);
          // Best-effort cleanup: without it a failed timeline leaves an empty
          // draft movie behind and a retry creates a second one. The uploaded
          // clips stay in the library. A failed cleanup must not replace the
          // timeline error the user needs to see.
          try {
            await fetch(browserApiPath(`/movies/${movie.id}`), { method: "DELETE" });
          } catch {
            // Ignore: the timeline error below is the one to report.
          }
          throw timelineError;
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
            {seconds(config.maxClipDurationSeconds)}s and {mb(config.maxClipSizeBytes)} MB each,
            {" "}{mb(config.maxUploadBytes)} MB per upload.
          </p>
        </div>
      </div>

      {error ? (
        <p className="form-error" role="alert">
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
