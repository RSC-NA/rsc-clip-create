"use client";

import Image from "next/image";
import { useRouter } from "next/navigation";
import { useState, useTransition } from "react";
import { browserApiPath } from "@/app/lib/browser-api";
import type { Logo } from "@/app/lib/types";

export function LogoLibrary({ logos }: { logos: Logo[] }) {
  const router = useRouter();
  const [team, setTeam] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isPending, startTransition] = useTransition();

  function upload() {
    startTransition(async () => {
      setError(null);
      try {
        if (!team.trim()) throw new Error("Enter the team name to match on.");
        if (!file) throw new Error("Choose an image file.");

        const body = new FormData();
        body.append("team", team.trim());
        body.append("file", file);

        const response = await fetch(browserApiPath("/logos"), {
          method: "POST",
          body,
        });
        if (!response.ok) {
          throw new Error(await response.text());
        }

        setTeam("");
        setFile(null);
        router.refresh();
      } catch (caught) {
        setError(
          caught instanceof Error ? caught.message : "Failed to upload the logo.",
        );
      }
    });
  }

  function remove(logoId: string) {
    startTransition(async () => {
      setError(null);
      try {
        const response = await fetch(browserApiPath(`/logos/${logoId}`), {
          method: "DELETE",
        });
        if (!response.ok) {
          throw new Error(await response.text());
        }
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
          <span className="eyebrow">Add or replace</span>
          <p className="text-soft">
            Uploading for a team that already has a logo replaces it. Matching
            ignores case and surrounding spaces.
          </p>
        </div>

        <div className="form-grid md:grid-cols-2">
          <div className="field">
            <label htmlFor="logo-team">Team</label>
            <input
              id="logo-team"
              onChange={(event) => setTeam(event.target.value)}
              placeholder="Falcons"
              value={team}
            />
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
        </div>

        {error ? (
          <p className="badge" data-tone="danger">
            {error}
          </p>
        ) : null}

        <div className="inline-actions">
          <button
            className="button button-primary"
            disabled={isPending || !team.trim() || !file}
            onClick={upload}
            type="button"
          >
            {isPending ? "Uploading..." : "Save Logo"}
          </button>
        </div>
      </section>

      <section className="table-card">
        {logos.length === 0 ? (
          <div className="empty-state">
            No logos yet. Clips will render with a text-only label.
          </div>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Team</th>
                <th>Logo</th>
                <th>File</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {logos.map((logo) => (
                <tr key={logo.id}>
                  <td>
                    <strong>{logo.team}</strong>
                  </td>
                  <td>
                    <Image
                      alt={`${logo.team} logo`}
                      height={48}
                      src={browserApiPath(`/logos/${logo.id}/file`)}
                      style={{ objectFit: "contain", background: "#0b1220" }}
                      unoptimized
                      width={96}
                    />
                  </td>
                  <td className="text-soft">{logo.originalName}</td>
                  <td>
                    <button
                      className="button button-secondary"
                      disabled={isPending}
                      onClick={() => remove(logo.id)}
                      type="button"
                    >
                      Remove
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  );
}
