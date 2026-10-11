// Copyright (c) 2026 Adam Krúpa and Ondra Csajka. MIT License.
// Single state store per contract chapter 12. Every panel reads from here.
// Run status is derived from events only (see derive.ts), never from HTTP responses.
import { api, ApiError, wsUrl } from "./api";
import type { RunEvent, SkillInfo } from "./types";

export interface RunState {
  run_id: string;
  events: RunEvent[]; // sorted by seq, no duplicates
  last_seq: number; // highest seq with no gaps before it
  created_at: string | null;
}

export type Connection = "connecting" | "open" | "reconnecting";

export interface StoreState {
  runs: Record<string, RunState>;
  skills: SkillInfo[]; // base list from GET /skills
  connection: Connection;
  loaded: boolean;
  syncError: string | null;
}

export const INITIAL_STATE: StoreState = {
  runs: {},
  skills: [],
  connection: "connecting",
  loaded: false,
  syncError: null,
};

const BACKOFF_MS = [1000, 2000, 4000, 8000, 10000];

function isEvent(x: unknown): x is RunEvent {
  if (!x || typeof x !== "object") return false;
  const e = x as Record<string, unknown>;
  return (
    typeof e.type === "string" &&
    typeof e.run_id === "string" &&
    typeof e.seq === "number" &&
    Number.isInteger(e.seq) &&
    e.seq >= 1
  );
}

function normalize(e: RunEvent): RunEvent {
  return {
    ...e,
    timestamp: typeof e.timestamp === "string" ? e.timestamp : "",
    phase: typeof e.phase === "string" ? e.phase : "",
    message: typeof e.message === "string" ? e.message : "",
    data: e.data && typeof e.data === "object" && !Array.isArray(e.data) ? e.data : {},
  };
}

function contiguousLastSeq(events: RunEvent[]): number {
  let last = 0;
  for (const e of events) {
    if (e.seq === last + 1) last = e.seq;
    else if (e.seq > last + 1) break;
  }
  return last;
}

export class Engine {
  private state: StoreState = INITIAL_STATE;
  private listeners = new Set<() => void>();
  private ws: WebSocket | null = null;
  private stopped = true;
  private ready = false; // history loaded, buffer flushed
  private buffer: RunEvent[] = [];
  private attempt = 0;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private retryTimer: ReturnType<typeof setTimeout> | null = null;
  private syncing = false;
  private syncAgain = false;
  private fetching = new Map<string, boolean>(); // run_id -> needs another fetch

  subscribe = (fn: () => void) => {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  };

  getSnapshot = () => this.state;

  private set(patch: Partial<StoreState>) {
    this.state = { ...this.state, ...patch };
    this.listeners.forEach((fn) => fn());
  }

  start() {
    if (!this.stopped) return;
    this.stopped = false;
    // Contract 12.2: open the WebSocket first, then load history.
    this.connect();
  }

  stop() {
    this.stopped = true;
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    if (this.retryTimer) clearTimeout(this.retryTimer);
    const ws = this.ws;
    this.ws = null;
    ws?.close();
  }

  // ---------- WebSocket ----------

  private connect() {
    if (this.stopped) return;
    let ws: WebSocket;
    try {
      ws = new WebSocket(wsUrl());
    } catch {
      this.scheduleReconnect();
      return;
    }
    this.ws = ws;
    ws.onopen = () => {
      if (this.ws !== ws) return;
      this.attempt = 0;
      this.set({ connection: "open" });
      // The handshake must finish before the history snapshot is requested.
      // This also fetches missed events after every reconnect (contract 12.4).
      void this.resync();
    };
    ws.onmessage = (msg) => {
      if (this.ws !== ws) return;
      let parsed: unknown;
      try {
        parsed = JSON.parse(String(msg.data));
      } catch {
        return;
      }
      if (!isEvent(parsed)) return;
      const ev = normalize(parsed);
      if (this.ready) this.ingest(ev);
      else this.buffer.push(ev);
    };
    ws.onclose = () => {
      if (this.ws !== ws) return;
      this.ws = null;
      this.scheduleReconnect();
    };
  }

  private scheduleReconnect() {
    if (this.stopped) return;
    const delay = BACKOFF_MS[Math.min(this.attempt, BACKOFF_MS.length - 1)];
    this.attempt += 1;
    this.set({ connection: "reconnecting" });
    this.reconnectTimer = setTimeout(() => this.connect(), delay);
  }

  // ---------- HTTP sync ----------

  async resync(): Promise<void> {
    if (this.stopped) return;
    if (this.syncing) {
      this.syncAgain = true;
      return;
    }
    this.syncing = true;
    try {
      const { runs } = await api.listRuns();
      for (const info of Array.isArray(runs) ? runs : []) {
        if (typeof info?.run_id !== "string") continue;
        this.ensureRun(info.run_id, typeof info.created_at === "string" ? info.created_at : null);
      }
      await Promise.all(Object.keys(this.state.runs).map((id) => this.fetchMissing(id)));

      if (!this.ready) {
        this.ready = true;
        const buffered = this.buffer;
        this.buffer = [];
        buffered.forEach((e) => this.ingest(e));
      }

      const { skills } = await api.skills();
      this.set({
        skills: Array.isArray(skills) ? skills : [],
        loaded: true,
        syncError: null,
      });
    } catch (e) {
      this.set({ syncError: e instanceof ApiError ? e.message : "Sync with the backend failed." });
      if (this.retryTimer) clearTimeout(this.retryTimer);
      this.retryTimer = setTimeout(() => void this.resync(), 3000);
    } finally {
      this.syncing = false;
      if (this.syncAgain) {
        this.syncAgain = false;
        void this.resync();
      }
    }
  }

  private async fetchMissing(runId: string): Promise<void> {
    if (this.fetching.has(runId)) {
      this.fetching.set(runId, true);
      return;
    }
    this.fetching.set(runId, false);
    try {
      for (;;) {
        const run = this.state.runs[runId];
        if (!run) return;
        try {
          const { events } = await api.events(runId, run.last_seq);
          this.merge(runId, (Array.isArray(events) ? events : []).filter(isEvent).map(normalize));
        } catch (e) {
          if (e instanceof ApiError && e.status === 404 && e.code === "RUN_NOT_FOUND") {
            // Backend restarted: drop the run from view.
            this.removeRun(runId);
            return;
          }
          throw e;
        }
        if (!this.fetching.get(runId)) return;
        this.fetching.set(runId, false);
      }
    } finally {
      this.fetching.delete(runId);
    }
  }

  // ---------- Store ----------

  private ensureRun(runId: string, createdAt: string | null) {
    const existing = this.state.runs[runId];
    if (existing) {
      if (!existing.created_at && createdAt) {
        this.set({ runs: { ...this.state.runs, [runId]: { ...existing, created_at: createdAt } } });
      }
      return;
    }
    this.set({
      runs: {
        ...this.state.runs,
        [runId]: { run_id: runId, events: [], last_seq: 0, created_at: createdAt },
      },
    });
  }

  private removeRun(runId: string) {
    if (!this.state.runs[runId]) return;
    const runs = { ...this.state.runs };
    delete runs[runId];
    this.set({ runs });
  }

  private merge(runId: string, incoming: RunEvent[]) {
    const run = this.state.runs[runId];
    if (!run) return;
    const bySeq = new Map<number, RunEvent>();
    for (const e of run.events) bySeq.set(e.seq, e);
    let changed = false;
    for (const e of incoming) {
      if (e.run_id !== runId || bySeq.has(e.seq)) continue;
      bySeq.set(e.seq, e);
      changed = true;
    }
    if (!changed) return;
    const events = [...bySeq.values()].sort((a, b) => a.seq - b.seq);
    this.set({
      runs: {
        ...this.state.runs,
        [runId]: {
          ...run,
          events,
          last_seq: contiguousLastSeq(events),
          created_at: run.created_at ?? (events[0]?.timestamp || null),
        },
      },
    });
  }

  // Contract 12.3: handle one incoming event.
  private ingest(ev: RunEvent) {
    const run = this.state.runs[ev.run_id];
    if (run?.events.some((e) => e.seq === ev.seq)) return;
    if (!run) this.ensureRun(ev.run_id, ev.timestamp || null);
    this.merge(ev.run_id, [ev]);
    const updated = this.state.runs[ev.run_id];
    if (updated && updated.events.some((e) => e.seq > updated.last_seq)) {
      void this.fetchMissing(ev.run_id).catch(() => {
        // A transient HTTP failure must not leave the timeline incomplete
        // indefinitely when the WebSocket remains connected.
        if (this.stopped) return;
        if (this.retryTimer) clearTimeout(this.retryTimer);
        this.retryTimer = setTimeout(() => void this.resync(), 3000);
      });
    }
  }
}
