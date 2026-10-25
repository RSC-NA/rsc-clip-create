"use client";

import { useEffect, useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import type {
  ClipSummary,
  MovieDetail,
  MovieSlot,
  RenderJob,
} from "@/app/lib/types";
import { browserApiPath } from "@/app/lib/browser-api";
import { readApiError } from "@/app/lib/api-error";
import { StatusBadge } from "@/app/components/status-badge";

type EditableSlot = MovieSlot;

function createEmptySlot(sortIndex: number): EditableSlot {
  return {
    id: crypto.randomUUID(),
    sortIndex,
    overlayText: "",
    clipId: null,
  };
}

function normalizeSlots(slots: EditableSlot[]) {
  return slots.map((slot, index) => ({
    ...slot,
    sortIndex: index,
  }));
}

function describeClip(clip: ClipSummary) {
  // The dropdown showed only the filename, though ClipSummary already carries
  // the parsed metadata and duration.
  const parts = [clip.team, clip.player, clip.tier].filter(Boolean).join(" / ");
  const seconds =
    clip.durationSeconds != null ? ` · ${clip.durationSeconds.toFixed(1)}s` : "";
  return parts ? `${parts}${seconds}` : clip.name;
}

export function TimelineEditor({
  movieId,
  initialSlots,
  availableClips,
  initialJob,
  minClipCount,
}: {
  movieId: string;
  initialSlots: MovieSlot[];
  availableClips: ClipSummary[];
  initialJob: RenderJob | null;
  minClipCount: number;
}) {
  const router = useRouter();
  const [slots, setSlots] = useState<EditableSlot[]>(
    initialSlots.length > 0
      ? normalizeSlots(initialSlots)
      : Array.from({ length: 5 }, (_, index) => createEmptySlot(index)),
  );
  const [job, setJob] = useState(initialJob);
  const [dirty, setDirty] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [isPending, startTransition] = useTransition();

  // Closed over as primitives, not the job object: setJob inside the interval
  // replaces `job` on every successful poll, so depending on the object would
  // tear the interval down and recreate it every 1.5s.
  const jobId = job?.id;
  const jobStatus = job?.status;
  // The backend answers 409 to both PUT /timeline and POST /render while a
  // job is queued or running; the poll below clears this once it finishes.
  const renderActive = jobStatus === "queued" || jobStatus === "running";

  useEffect(() => {
    if (!jobId || !["queued", "running"].includes(jobStatus ?? "")) return;
    const timer = window.setInterval(async () => {
      const response = await fetch(browserApiPath(`/jobs/${jobId}`), {
        cache: "no-store",
      });
      if (response.ok) setJob((await response.json()) as RenderJob);
    }, 1500);
    return () => window.clearInterval(timer);
  }, [jobId, jobStatus]);

  useEffect(() => {
    if (!dirty) return;
    const warn = (event: BeforeUnloadEvent) => event.preventDefault();
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  function updateSlot(slotId: string, patch: Partial<EditableSlot>) {
    setDirty(true);
    setSlots((current) =>
      current.map((slot) => (slot.id === slotId ? { ...slot, ...patch } : slot)),
    );
  }

  function insertSlot(index: number) {
    setDirty(true);
    setSlots((current) =>
      normalizeSlots([
        ...current.slice(0, index),
        createEmptySlot(index),
        ...current.slice(index),
      ]),
    );
  }

  function appendSlot() {
    setDirty(true);
    setSlots((current) =>
      normalizeSlots([...current, createEmptySlot(current.length)]),
    );
  }

  function removeSlot(slotId: string) {
    setDirty(true);
    setSlots((current) => normalizeSlots(current.filter((slot) => slot.id !== slotId)));
  }

  function moveSlot(slotId: string, direction: -1 | 1) {
    setDirty(true);
    setSlots((current) => {
      const index = current.findIndex((slot) => slot.id === slotId);
      const target = index + direction;
      if (index < 0 || target < 0 || target >= current.length) return current;
      const next = [...current];
      [next[index], next[target]] = [next[target], next[index]];
      return normalizeSlots(next);
    });
  }

  function saveTimeline() {
    if (renderActive) return;
    startTransition(async () => {
      setError(null);
      try {
        const response = await fetch(browserApiPath(`/movies/${movieId}/timeline`), {
          method: "PUT",
          headers: {
            "content-type": "application/json",
          },
          body: JSON.stringify({
            slots: normalizeSlots(slots).map((slot) => ({
              id: slot.id,
              sort_index: slot.sortIndex,
              clip_id: slot.clipId,
              overlay_text: slot.overlayText,
            })),
          }),
        });

        if (!response.ok) {
          throw await readApiError(response);
        }

        // The server mints its own slot ids. Without adopting them, this
        // component keeps its local crypto.randomUUID() ids forever --
        // router.refresh() re-renders the Server Component but does not remount
        // this one -- and the next save creates duplicate slots.
        const saved = (await response.json()) as MovieDetail;
        setSlots(normalizeSlots(saved.slots));
        setDirty(false);
        router.refresh();
      } catch (caught) {
        setError(caught instanceof Error ? caught.message : "Failed to save timeline.");
      }
    });
  }

  function queueRender() {
    if (renderActive) return;
    startTransition(async () => {
      setError(null);
      try {
        const response = await fetch(browserApiPath(`/movies/${movieId}/render`), {
          method: "POST",
        });

        if (!response.ok) {
          throw await readApiError(response);
        }

        const payload = (await response.json()) as RenderJob;
        setJob(payload);
        router.refresh();
      } catch (caught) {
        setError(caught instanceof Error ? caught.message : "Failed to queue render.");
      }
    });
  }

  const filledSlots = slots.filter((slot) => slot.clipId).length;

  return (
    <div className="page-grid">
      <section className="panel stack-md">
        <div className="row-between">
          <div className="stack-sm">
            <span className="eyebrow">Timeline</span>
            <p className="text-soft">
              Review the uploaded clips in order, remove unwanted clips, and add new
              positions when needed.
            </p>
          </div>
          <div className="inline-actions">
            <button className="button button-secondary" onClick={appendSlot} type="button">
              Add Slot
            </button>
            <button
              className="button button-primary"
              disabled={isPending || renderActive}
              onClick={saveTimeline}
              type="button"
            >
              {isPending ? "Saving..." : "Save Timeline"}
            </button>
          </div>
        </div>

        <div className="editor-grid">
          {slots.map((slot, index) => (
            <article className="editor-slot stack-md" key={slot.id}>
              <header>
                <div>
                  <span className="mono-label">Slot {index + 1}</span>
                  <p className="text-soft">Ordered position {slot.sortIndex + 1}</p>
                </div>
                <div className="slot-actions">
                  <button
                    className="button button-ghost"
                    disabled={index === 0}
                    onClick={() => moveSlot(slot.id, -1)}
                    type="button"
                  >
                    Move Up
                  </button>
                  <button
                    className="button button-ghost"
                    disabled={index === slots.length - 1}
                    onClick={() => moveSlot(slot.id, 1)}
                    type="button"
                  >
                    Move Down
                  </button>
                  <button
                    className="button button-ghost"
                    onClick={() => insertSlot(index)}
                    type="button"
                  >
                    Insert Before
                  </button>
                  <button
                    className="button button-ghost"
                    onClick={() => insertSlot(index + 1)}
                    type="button"
                  >
                    Insert After
                  </button>
                  <button
                    className="button button-secondary"
                    onClick={() => removeSlot(slot.id)}
                    type="button"
                  >
                    Remove
                  </button>
                </div>
              </header>

              <div className="form-grid md:grid-cols-2">
                <div className="field">
                  <label htmlFor={`clip-${slot.id}`}>Clip</label>
                  <select
                    id={`clip-${slot.id}`}
                    onChange={(event) =>
                      updateSlot(slot.id, { clipId: event.target.value || null })
                    }
                    value={slot.clipId ?? ""}
                  >
                    <option value="">Choose a clip</option>
                    {availableClips.map((clip) => {
                      const usedElsewhere = slots.some(
                        (other) => other.id !== slot.id && other.clipId === clip.id,
                      );
                      return (
                        <option key={clip.id} value={clip.id}>
                          {describeClip(clip)}
                          {usedElsewhere ? " — already used" : ""}
                        </option>
                      );
                    })}
                  </select>
                </div>
                {slot.clipId ? (() => {
                  const clip = availableClips.find((item) => item.id === slot.clipId);
                  if (!clip) return null;
                  return (
                    <p className="text-soft">
                      Label: {clip.team ?? "?"} · {clip.player ?? "?"} ·{" "}
                      {clip.tier ?? "?"}
                    </p>
                  );
                })() : null}

                <div className="field">
                  <label htmlFor={`text-${slot.id}`}>Overlay Text</label>
                  <input
                    id={`text-${slot.id}`}
                    onChange={(event) =>
                      updateSlot(slot.id, { overlayText: event.target.value })
                    }
                    placeholder="Intro line, chapter marker, CTA, subtitle"
                    value={slot.overlayText}
                  />
                </div>
              </div>
            </article>
          ))}
        </div>
      </section>

      <section className="panel stack-md">
        <div className="stack-sm">
          <span className="eyebrow">Transitions</span>
          <p className="text-soft">
            Every cut is covered by the brand sweep. It plays over the join
            rather than between the clips, so a transition adds no time to the
            movie: the finished length is exactly the clips. Replace the sweep
            on the Brand Sweep page. Clips shorter than the sweep itself cut
            straight through, with nothing over them.
          </p>
        </div>
      </section>

      <section className="panel stack-md">
        <div className="row-between">
          <div className="stack-sm">
            <span className="eyebrow">Render Job</span>
            <p className="text-soft">
              Queue FFmpeg rendering and inspect clip-level failures from the latest run.
            </p>
          </div>
          <div className="stack-sm">
            <button
              className="button button-primary"
              disabled={isPending || filledSlots < minClipCount || dirty || renderActive}
              onClick={queueRender}
              type="button"
            >
              Queue Render
            </button>
            {renderActive ? (
              <p className="text-soft">
                Wait for the current render to finish before saving or rendering.
              </p>
            ) : null}
            {dirty ? (
              <p className="text-soft">Save the timeline before rendering.</p>
            ) : null}
          </div>
        </div>

        {error ? <p className="form-error" role="alert">{error}</p> : null}

        {job ? (
          <div className="stack-md">
            <div className="row-between">
              <div>
                <strong>Current job state</strong>
                <p className="text-soft">{job.finalStatusReason ?? "Waiting for worker activity."}</p>
              </div>
              <StatusBadge value={job.status} />
            </div>
            {job.status === "succeeded" ? (
              <p className="text-soft">Your MP4 is ready on the Movies page for playback and download.</p>
            ) : null}

            <div className="cards-grid">
              <article className="muted-panel panel">
                <span className="mono-label">Accepted Clips</span>
                <strong>{job.acceptedClipCount}</strong>
              </article>
              <article className="muted-panel panel">
                <span className="mono-label">Failed Clips</span>
                <strong>{job.failedClipCount}</strong>
              </article>
            </div>

            <div className="detail-list">
              {job.attempts.map((attempt) => (
                <article key={attempt.id}>
                  <div className="row-between">
                    <div>
                      <strong>Attempt {attempt.attemptNumber}</strong>
                      <p className="text-soft">
                        {attempt.errorSummary ?? "No provider summary recorded."}
                      </p>
                    </div>
                    <StatusBadge value={attempt.status} />
                  </div>

                  <div className="stack-sm">
                    {attempt.clipResults.map((clipResult) => (
                      <div className="row-between" key={clipResult.id}>
                        <div>
                          <strong>{clipResult.clipName}</strong>
                          <p className="text-soft">
                            {clipResult.reasonSummary ?? "Accepted by provider."}
                          </p>
                        </div>
                        <StatusBadge value={clipResult.status} />
                      </div>
                    ))}
                  </div>
                </article>
              ))}
            </div>
          </div>
        ) : (
          <div className="empty-state">
            No render has been queued for this movie yet.
          </div>
        )}
      </section>
    </div>
  );
}
