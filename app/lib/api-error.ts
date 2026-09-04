/**
 * FastAPI returns two incompatible shapes under `detail` on the same status
 * code: a string for HTTPException, and an array of objects for Pydantic
 * validation errors. Client components previously rendered `response.text()`
 * raw, so a user with an empty title saw the whole JSON document -- uppercased,
 * because it went into a `.badge` pill.
 */
type ValidationItem = {
  loc?: (string | number)[];
  msg?: string;
};

function fieldName(item: ValidationItem): string | null {
  const parts = (item.loc ?? []).filter(
    (part) => typeof part === "string" && part !== "body",
  );
  return parts.length > 0 ? String(parts[parts.length - 1]) : null;
}

export function formatApiError(status: number, body: string): string {
  if (status === 401) {
    return "Your session has expired. Sign in again to continue.";
  }
  if (status === 413) {
    return "That upload is too large.";
  }

  let parsed: unknown;
  try {
    parsed = JSON.parse(body);
  } catch {
    // A proxy 502 returns an HTML document; never show markup to the user.
    return status >= 500
      ? "The server had a problem completing that request. Try again shortly."
      : body.trim().slice(0, 200) || `Request failed (${status}).`;
  }

  const detail = (parsed as { detail?: unknown })?.detail;

  if (typeof detail === "string") return detail;

  if (Array.isArray(detail)) {
    const messages = (detail as ValidationItem[])
      .map((item) => {
        const field = fieldName(item);
        const message = item.msg ?? "is invalid";
        return field ? `${field}: ${message}` : message;
      })
      .filter(Boolean);
    if (messages.length > 0) return messages.join("; ");
  }

  return status >= 500
    ? "The server had a problem completing that request. Try again shortly."
    : `Request failed (${status}).`;
}

/** Reads a failed Response into a human-readable message. */
export async function readApiError(response: Response): Promise<Error> {
  let body = "";
  try {
    body = await response.text();
  } catch {
    body = "";
  }
  return new Error(formatApiError(response.status, body));
}
