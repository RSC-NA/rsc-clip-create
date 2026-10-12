"use client";

import { useRouter } from "next/navigation";
import { useRef, useState, useTransition } from "react";
import { readApiError } from "@/app/lib/api-error";
import { browserApiPath } from "@/app/lib/browser-api";
import type { BrandSweep as BrandSweepFile } from "@/app/lib/types";

export function BrandSweep({
  sweep,
  supportedFormats,
}: {
  sweep: BrandSweepFile | null;
  supportedFormats: string[];
}) {
  const router = useRouter();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isPending, startTransition] = useTransition();

  const accept = supportedFormats.map((format) => `.${format}`).join(",");

  function upload() {
    startTransition(async () => {
      setError(null);
      try {
        if (!file) throw new Error("Choose a video file.");

        const body = new FormData();
        body.append("file", file);

        const response = await fetch(browserApiPath("/sweeps"), {
          method: "POST",
          body,
        });
        if (!response.ok) throw await readApiError(response);

        if (fileInputRef.current) fileInputRef.current.value = "";
        setFile(null);
        router.refresh();
      } catch (caught) {
        setError(
          caught instanceof Error ? caught.message : "Failed to upload the sweep.",
        );
      }
    });
  }

  function remove() {
    if (!sweep) return;
    startTransition(async () => {
      setError(null);
      try {
        const response = await fetch(browserApiPath(`/sweeps/${sweep.id}`), {
          method: "DELETE",
        });
        if (!response.ok) throw await readApiError(response);
        router.refresh();
      } catch (caught) {
        setError(
          caught instanceof Error ? caught.message : "Failed to remove the sweep.",
        );
      }
    });
  }

  return (
    <div className="page-grid">
      <section className="panel stack-md">
        <div className="stack-sm">
          <span className="eyebrow">Current sweep</span>
          <p className="text-soft">
            Played over every cut between clips. The clips cut while the sweep
            covers the picture, so it hides the join and costs the movie no
            extra running time.
          </p>
        </div>

        {sweep ? (
          <div className="row-between">
            {/* Muted and looping: a one-second transition being inspected,
                not something anyone wants to sit through with sound. */}
            <video
              autoPlay
              loop
              muted
              playsInline
              src={browserApiPath(`/sweeps/${sweep.id}/file`)}
              style={{
                background: "#0b1220",
                borderRadius: 8,
                maxWidth: 320,
                width: "100%",
              }}
            />
            <div className="stack-sm">
              <span className="text-soft">{sweep.originalName}</span>
              {sweep.durationSeconds != null ? (
                <span className="text-soft">
                  {sweep.durationSeconds.toFixed(2)}s
                </span>
              ) : null}
              <button
                aria-label="Remove the brand sweep"
                className="button button-secondary"
                disabled={isPending}
                onClick={remove}
                type="button"
              >
                Remove
              </button>
            </div>
          </div>
        ) : (
          <div className="empty-state">
            Using the bundled sweep. Upload one here to replace it.
          </div>
        )}
      </section>

      <section className="panel stack-md">
        <div className="stack-sm">
          <span className="eyebrow">{sweep ? "Replace it" : "Upload one"}</span>
          <p className="text-soft">
            A stinger with an alpha channel, {supportedFormats.join(", ")}. It
            should start transparent, cover the frame completely somewhere in
            the middle — that is where the clips cut — and clear again by the
            end. Any audio on it is ignored.
          </p>
        </div>

        <div className="field">
          <label htmlFor="sweep-file">Video</label>
          <input
            accept={accept}
            id="sweep-file"
            onChange={(event) => setFile(event.target.files?.[0] ?? null)}
            ref={fileInputRef}
            type="file"
          />
        </div>

        {error ? (
          <p className="form-error" role="alert">
            {error}
          </p>
        ) : null}

        <div className="inline-actions">
          <button
            className="button button-primary"
            disabled={isPending || !file}
            onClick={upload}
            type="button"
          >
            {isPending ? "Uploading..." : sweep ? "Replace Sweep" : "Save Sweep"}
          </button>
        </div>
      </section>
    </div>
  );
}
