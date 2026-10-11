// Copyright (c) 2026 Adam Krúpa and Ondra Csajka. MIT License.
"use client";

import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import type { FormEvent } from "react";
import type { RunState } from "@/lib/engine";
import { formatRelative, runCreatedAt, runRequest, runStatus, STATUS_LABEL } from "@/lib/derive";

export interface RunMetadata {
  title?: string;
  pinned?: boolean;
  archived?: boolean;
}

export interface RunOrganization {
  metadata: Record<string, RunMetadata>;
  titleFor: (run: RunState) => string;
  update: (id: string, patch: Partial<RunMetadata>) => void;
  visibleRuns: RunState[];
}

// Display organization belongs to this mounted workspace. It does not change
// the backend's run records or write prompts to browser storage.
export function useRunOrganization(runs: RunState[]): RunOrganization {
  const [metadata, setMetadata] = useState<Record<string, RunMetadata>>({});
  const titleFor = useCallback((run: RunState) => {
    const lines = (runRequest(run) ?? "").split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
    if (lines[0] === "Detection request:") lines.shift();
    return metadata[run.run_id]?.title || lines[0]?.slice(0, 100) || `Run ${run.run_id.slice(0, 8)}`;
  }, [metadata]);

  const update = useCallback((id: string, patch: Partial<RunMetadata>) => {
    const normalized = { ...patch };
    if (typeof normalized.title === "string") normalized.title = normalized.title.trim().slice(0, 100) || undefined;
    setMetadata((current) => ({ ...current, [id]: { ...current[id], ...normalized } }));
  }, []);

  const visibleRuns = useMemo(() => runs
    .filter((run) => !metadata[run.run_id]?.archived)
    .sort((a, b) => Number(Boolean(metadata[b.run_id]?.pinned)) - Number(Boolean(metadata[a.run_id]?.pinned))), [runs, metadata]);

  return { metadata, titleFor, update, visibleRuns };
}

interface RunHistoryDialogProps {
  open: boolean;
  onClose: () => void;
  runs: RunState[];
  organization: RunOrganization;
  onSelect: (id: string) => void;
}

export function RunHistoryDialog({ open, ...props }: RunHistoryDialogProps) {
  return open ? <RunHistoryModal {...props} /> : null;
}

type HistoryFilter = "all" | "pinned" | "archived";

function RunHistoryModal({ onClose, runs, organization, onSelect }: Omit<RunHistoryDialogProps, "open">) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const renameButtons = useRef(new Map<string, HTMLButtonElement>());
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<HistoryFilter>("all");
  const [renameId, setRenameId] = useState<string | null>(null);
  const [announcement, setAnnouncement] = useState("");
  const { metadata, titleFor, update } = organization;

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    const trigger = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    dialog.showModal();
    searchRef.current?.focus();
    return () => {
      const focus = document.activeElement;
      const restoreFocus = focus === document.body || (focus instanceof Node && dialog.contains(focus));
      dialog.close();
      if (!restoreFocus) return;
      if (trigger?.isConnected && trigger.getClientRects().length) trigger.focus({ preventScroll: true });
      else document.querySelector<HTMLButtonElement>("#menu-toggle")?.focus({ preventScroll: true });
    };
  }, []);

  const matches = useMemo(() => {
    const search = query.trim().toLocaleLowerCase("en-US");
    return runs.filter((run) => {
      const entry = metadata[run.run_id];
      const inFilter = filter === "archived" ? entry?.archived : !entry?.archived && (filter !== "pinned" || entry?.pinned);
      return inFilter && `${titleFor(run)} ${runRequest(run) || ""} ${run.run_id}`.toLocaleLowerCase("en-US").includes(search);
    }).sort((a, b) => Number(Boolean(metadata[b.run_id]?.pinned)) - Number(Boolean(metadata[a.run_id]?.pinned)));
  }, [runs, metadata, titleFor, query, filter]);

  const finishRename = (id: string) => {
    setRenameId(null);
    requestAnimationFrame(() => (renameButtons.current.get(id) || searchRef.current)?.focus());
  };

  return (
    <dialog
      ref={dialogRef}
      id="history-dialog"
      className="history-dialog"
      aria-labelledby="history-title"
      aria-describedby="history-description"
      onCancel={(event) => {
        event.preventDefault();
        if (renameId) finishRename(renameId);
        else onClose();
      }}
      onClick={(event) => {
        if (event.target !== event.currentTarget) return;
        const bounds = event.currentTarget.getBoundingClientRect();
        if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) onClose();
      }}
    >
      <div className="history-header">
        <div>
          <h2 id="history-title">Search runs</h2>
          <p id="history-description">Find and organize your detection runs.</p>
        </div>
        <button type="button" className="icon-button history-close" aria-label="Close run history" onClick={onClose}>&times;</button>
      </div>
      <div className="history-controls">
        <label className="sr-only" htmlFor="history-search">Search by title, prompt, or run ID</label>
        <input
          ref={searchRef}
          id="history-search"
          className="history-search"
          type="search"
          autoComplete="off"
          maxLength={200}
          value={query}
          placeholder="Search by title, prompt, or run ID"
          aria-controls="history-results"
          onChange={(event) => { setQuery(event.target.value); setRenameId(null); setAnnouncement(""); }}
        />
        <div className="history-filters" role="group" aria-label="Filter run history">
          {(["all", "pinned", "archived"] as const).map((value) => (
            <button type="button" key={value} className="history-filter" aria-pressed={filter === value} onClick={() => { setFilter(value); setRenameId(null); setAnnouncement(""); }}>
              {value === "all" ? "All" : value === "pinned" ? "Pinned" : "Archived"}
            </button>
          ))}
        </div>
      </div>
      <p className="history-result-count" role="status">
        {announcement || `${matches.length} ${matches.length === 1 ? "run" : "runs"}${filter === "archived" ? " in archive" : filter === "pinned" ? " pinned" : " in history"}.`}
      </p>
      <div id="history-results" className="history-results">
        {matches.map((run) => {
          const entry = metadata[run.run_id];
          const status = runStatus(run);
          const stateClass = status === "awaiting_approval" ? "review" : status === "running" ? "working" : status || "";
          const title = titleFor(run);
          return (
            <article key={run.run_id} className="history-result">
              <button type="button" className="history-result-open" onClick={() => { dialogRef.current?.close(); onClose(); onSelect(run.run_id); }}>
                <span className="history-result-top"><strong>{title}</strong>{entry?.pinned && <span className="history-pinned-label">Pinned</span>}</span>
                <span className="history-result-prompt">{runRequest(run) || "The request has not been received yet."}</span>
                <span className="history-result-meta">
                  <span className="mono">{run.run_id}</span>
                  <span className={`state-label ${stateClass}`}>{status ? STATUS_LABEL[status] : "Waiting for events"}</span>
                  <span>{formatRelative(runCreatedAt(run))}</span>
                </span>
              </button>
              <div className="history-result-actions">
                <button
                  ref={(button) => { if (button) renameButtons.current.set(run.run_id, button); else renameButtons.current.delete(run.run_id); }}
                  type="button"
                  className="history-action"
                  aria-label={`Rename ${title}`}
                  aria-expanded={renameId === run.run_id}
                  onClick={() => setRenameId(run.run_id)}
                >Rename</button>
                <button type="button" className="history-action" aria-pressed={Boolean(entry?.pinned)} aria-label={`${entry?.pinned ? "Unpin" : "Pin"} ${title}`} onClick={() => {
                  update(run.run_id, { pinned: !entry?.pinned });
                  setAnnouncement(entry?.pinned ? "Run unpinned." : "Run pinned.");
                  if (filter === "pinned" && entry?.pinned) searchRef.current?.focus();
                }}>{entry?.pinned ? "Unpin" : "Pin"}</button>
                <button type="button" className="history-action" aria-label={`${entry?.archived ? "Restore" : "Archive"} ${title}`} onClick={() => {
                  update(run.run_id, { archived: !entry?.archived });
                  setRenameId(null);
                  setAnnouncement(entry?.archived ? "Run restored to history." : "Run archived. Find it under Archived.");
                  searchRef.current?.focus();
                }}>{entry?.archived ? "Restore" : "Archive"}</button>
              </div>
              {renameId === run.run_id && <RenameRun title={title} onCancel={() => finishRename(run.run_id)} onSave={(value) => {
                update(run.run_id, { title: value });
                finishRename(run.run_id);
                setAnnouncement("Run renamed.");
              }} />}
            </article>
          );
        })}
        {!matches.length && <div className="history-empty">
          <strong>{query.trim() ? "No matching runs" : filter === "pinned" ? "No pinned runs" : filter === "archived" ? "Your archive is empty" : "No runs yet"}</strong>
          <p>{query.trim() ? "Try another title, phrase, or run ID." : filter === "pinned" ? "Pin a run to keep it at the top of your history." : filter === "archived" ? "Archived runs will appear here. Archiving does not delete them." : "Create a detection to get started, or restore a run from Archived."}</p>
        </div>}
      </div>
      <div className="history-footer">
        <span>Organization applies to this tab. Runs stay on the backend.</span>
        <span>Esc to close</span>
      </div>
    </dialog>
  );
}

function RenameRun({ title, onSave, onCancel }: { title: string; onSave: (title: string) => void; onCancel: () => void }) {
  const id = useId();
  const inputRef = useRef<HTMLInputElement>(null);
  const [value, setValue] = useState(title);
  const [error, setError] = useState("");

  useEffect(() => {
    inputRef.current?.focus();
    inputRef.current?.select();
  }, []);

  const save = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!value.trim()) { setError("Enter a title for this run."); inputRef.current?.focus(); return; }
    onSave(value.trim());
  };

  return (
    <form className="history-rename" onSubmit={save}>
      <label htmlFor={id}>Run title</label>
      <input ref={inputRef} id={id} value={value} maxLength={100} autoComplete="off" aria-invalid={Boolean(error)} aria-describedby={error ? `${id}-error` : undefined} onChange={(event) => { setValue(event.target.value); setError(""); }} />
      {error && <p id={`${id}-error`} role="alert">{error}</p>}
      <div className="history-rename-actions">
        <button type="submit" className="secondary-button">Save title</button>
        <button type="button" className="text-button" onClick={onCancel}>Cancel</button>
      </div>
    </form>
  );
}
