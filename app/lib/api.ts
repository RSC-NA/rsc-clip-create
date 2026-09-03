import "server-only";
import { cookies } from "next/headers";
import type {
  AdminDashboard,
  AdminError,
  AdminUser,
  AdminVideo,
  AppSession,
  ClientConfig,
  Logo,
  MovieDetail,
  MovieSummary,
  RenderJob,
} from "@/app/lib/types";

const internalOrigin =
  process.env.FASTAPI_INTERNAL_URL ?? "http://127.0.0.1:8000";

type ApiOptions = RequestInit & {
  allowUnauthorized?: boolean;
};

class ApiError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

async function request<T>(path: string, options: ApiOptions = {}): Promise<T> {
  const cookieStore = await cookies();
  const headers = new Headers(options.headers);
  const cookieHeader = cookieStore.toString();

  if (cookieHeader) {
    headers.set("cookie", cookieHeader);
  }

  if (!headers.has("accept")) {
    headers.set("accept", "application/json");
  }

  const response = await fetch(`${internalOrigin}/api/v1${path}`, {
    ...options,
    headers,
    cache: "no-store",
  });

  if (!response.ok) {
    if (response.status === 401 && options.allowUnauthorized) {
      return null as T;
    }

    const message = await response.text();
    throw new ApiError(message || "Backend request failed", response.status);
  }

  if (response.status === 204) {
    return null as T;
  }

  return (await response.json()) as T;
}

export async function getSession() {
  return request<AppSession | null>("/auth/session", {
    allowUnauthorized: true,
  });
}

export async function getMovies() {
  return request<MovieSummary[]>("/movies");
}

export async function getMovie(movieId: string) {
  return request<MovieDetail>(`/movies/${movieId}`);
}

export async function getJob(jobId: string) {
  return request<RenderJob>(`/jobs/${jobId}`);
}

export async function getLogos() {
  return request<Logo[]>("/logos");
}

export async function getClientConfig() {
  return request<ClientConfig>("/config");
}

export async function getAdminDashboard() {
  return request<AdminDashboard>("/admin/dashboard");
}

export async function getAdminUsers() {
  return request<AdminUser[]>("/admin/users");
}

export async function getAdminVideos() {
  return request<AdminVideo[]>("/admin/videos");
}

export async function getAdminErrors() {
  return request<AdminError[]>("/admin/errors");
}

export { ApiError };
