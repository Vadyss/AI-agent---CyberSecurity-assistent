// Copyright (c) 2026 Adam Krúpa and Ondra Csajka. MIT License.
// HTTP client per contract chapter 7. Calls the backend directly from the browser.
import type { RunEvent, RunInfo, SkillInfo } from "./types";

export const API_BASE = (process.env.NEXT_PUBLIC_API_BASE || "http://127.0.0.1:8000").replace(/\/+$/, "");

export function apiUrl(path: string): string {
  return `${API_BASE}${path}`;
}

export function wsUrl(): string {
  const url = new URL(`${API_BASE}/ws`);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  return url.toString();
}

export function audioUrl(runId: string): string {
  return apiUrl(`/runs/${encodeURIComponent(runId)}/audio`);
}

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(apiUrl(path), {
      signal: AbortSignal.timeout(15000),
      ...init,
      headers: init?.body ? { "Content-Type": "application/json" } : undefined,
      cache: "no-store",
    });
  } catch {
    throw new ApiError(0, "NETWORK_ERROR", "Can't reach the backend.");
  }
  let body: unknown = null;
  try {
    body = await res.json();
  } catch {
    // body is not JSON
  }
  if (!res.ok) {
    const err = (body as { error?: { code?: unknown; message?: unknown } } | null)?.error;
    throw new ApiError(
      res.status,
      typeof err?.code === "string" ? err.code : "UNKNOWN",
      typeof err?.message === "string" ? err.message : `Request failed (HTTP ${res.status}).`,
    );
  }
  return body as T;
}

export const api = {
  health: () => request<{ status: string; contract_version: number }>("/health"),
  createRun: (text: string) =>
    request<{ run_id: string; status: string }>("/runs", {
      method: "POST",
      body: JSON.stringify({ request: text }),
    }),
  listRuns: () => request<{ runs: RunInfo[] }>("/runs"),
  events: (runId: string, afterSeq: number) =>
    request<{ events: RunEvent[] }>(
      `/runs/${encodeURIComponent(runId)}/events?after_seq=${afterSeq}`,
    ),
  approve: (runId: string, comment: string) =>
    request<{ status: string }>(`/runs/${encodeURIComponent(runId)}/approve`, {
      method: "POST",
      body: JSON.stringify(comment ? { comment } : {}),
    }),
  reject: (runId: string, reason: string) =>
    request<{ status: string }>(`/runs/${encodeURIComponent(runId)}/reject`, {
      method: "POST",
      body: JSON.stringify({ reason }),
    }),
  skills: () => request<{ skills: SkillInfo[] }>("/skills"),
};
