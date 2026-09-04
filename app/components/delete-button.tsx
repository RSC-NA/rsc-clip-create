"use client";

import { useRouter } from "next/navigation";
import { useState, useTransition } from "react";
import { readApiError } from "@/app/lib/api-error";
import { browserApiPath } from "@/app/lib/browser-api";

/**
 * Two-step delete. The first click arms it, the second confirms, so a
 * destructive action never happens on a single stray click.
 */
export function DeleteButton({
  path,
  label,
  confirmLabel = "Confirm",
  onDeleted,
}: {
  path: string;
  label: string;
  confirmLabel?: string;
  onDeleted?: () => void;
}) {
  const router = useRouter();
  const [armed, setArmed] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [isPending, startTransition] = useTransition();

  function remove() {
    if (!armed) {
      setArmed(true);
      return;
    }
    startTransition(async () => {
      setError(null);
      try {
        const response = await fetch(browserApiPath(path), { method: "DELETE" });
        if (!response.ok) throw await readApiError(response);
        setArmed(false);
        onDeleted?.();
        router.refresh();
      } catch (caught) {
        setError(caught instanceof Error ? caught.message : "Delete failed.");
        setArmed(false);
      }
    });
  }

  return (
    <div className="stack-sm">
      <button
        aria-label={armed ? `${confirmLabel}: ${label}` : label}
        className={armed ? "button button-primary" : "button button-secondary"}
        disabled={isPending}
        onBlur={() => setArmed(false)}
        onClick={remove}
        type="button"
      >
        {isPending ? "Removing..." : armed ? confirmLabel : "Remove"}
      </button>
      {error ? (
        <p className="text-soft" role="alert">
          {error}
        </p>
      ) : null}
    </div>
  );
}
