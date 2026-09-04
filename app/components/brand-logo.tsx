"use client";

import Image from "next/image";
import { useRouter } from "next/navigation";
import { useState, useTransition } from "react";
import { readApiError } from "@/app/lib/api-error";
import { browserApiPath } from "@/app/lib/browser-api";
import type { Logo } from "@/app/lib/types";

export function BrandLogo({ logo }: { logo: Logo | null }) {
  const router = useRouter();
  const [file, setFile] = useState<File | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isPending, startTransition] = useTransition();

  function upload() {
    startTransition(async () => {
      setError(null);
      try {
        if (!file) throw new Error("Choose an image file.");

        const body = new FormData();
        body.append("file", file);

        const response = await fetch(browserApiPath("/logos"), {
          method: "POST",
          body,
        });
        if (!response.ok) throw await readApiError(response);

        setFile(null);
        router.refresh();
      } catch (caught) {
        setError(
          caught instanceof Error ? caught.message : "Failed to upload the logo.",
        );
      }
    });
  }

  function remove() {
    if (!logo) return;
    startTransition(async () => {
      setError(null);
      try {
        const response = await fetch(browserApiPath(`/logos/${logo.id}`), {
          method: "DELETE",
        });
        if (!response.ok) throw await readApiError(response);
        router.refresh();
      } catch (caught) {
        setError(
          caught instanceof Error ? caught.message : "Failed to remove the logo.",
        );
      }
    });
  }

  return (
    <div className="page-grid">
      <section className="panel stack-md">
        <div className="stack-sm">
          <span className="eyebrow">Current logo</span>
          <p className="text-soft">
            Shown in the centre of every transition. Movies without one render
            plain sweeps.
          </p>
        </div>

        {logo ? (
          <div className="row-between">
            <Image
              alt="Brand logo"
              height={120}
              src={browserApiPath(`/logos/${logo.id}/file`)}
              style={{ objectFit: "contain", background: "#0b1220", borderRadius: 8 }}
              unoptimized
              width={220}
            />
            <div className="stack-sm">
              <span className="text-soft">{logo.originalName}</span>
              <button
                aria-label="Remove the brand logo"
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
            No brand logo yet. Transitions render without a mark.
          </div>
        )}
      </section>

      <section className="panel stack-md">
        <div className="stack-sm">
          <span className="eyebrow">{logo ? "Replace it" : "Upload one"}</span>
          <p className="text-soft">
            PNG with transparency works best. Uploading replaces whatever is
            already there.
          </p>
        </div>

        <div className="field">
          <label htmlFor="logo-file">Image</label>
          <input
            accept="image/png,image/jpeg,image/webp,image/gif"
            id="logo-file"
            onChange={(event) => setFile(event.target.files?.[0] ?? null)}
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
            {isPending ? "Uploading..." : logo ? "Replace Logo" : "Save Logo"}
          </button>
        </div>
      </section>
    </div>
  );
}
