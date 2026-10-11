// Copyright (c) 2026 Adam Krúpa and Ondra Csajka. MIT License.
"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Compare } from "@/components/Compare";
import { DetectionRun } from "@/components/DetectionRun";
import { PreferencesDialog } from "@/components/Preferences";
import { RequestComposer } from "@/components/RequestComposer";
import { RunHistoryDialog, useRunOrganization } from "@/components/RunHistory";
import { IconArrowUp, IconHammer, IconPanel, IconPlus, IconRefresh, IconSidebar, IconX } from "@/components/icons";
import { StatusPill } from "@/components/ui";
import { api } from "@/lib/api";
import { formatRelative, installedSkills, isActive, runCreatedAt, runStatus, sortRuns, STATUS_LABEL } from "@/lib/derive";
import type { RunState } from "@/lib/engine";
import { PreferencesProvider } from "@/lib/preferences";
import { CONTRACT_VERSION } from "@/lib/types";
import { refreshStore, useStore } from "@/lib/useStore";

type View = "home" | "run" | "skills" | "compare";
type Health = "checking" | "ok" | "mismatch" | "down";
const VIEW_NAMES: Record<View, string> = { home: "Request editor", run: "Current run", skills: "Skill library", compare: "Compare runs" };
const TEMPLATES = [
  { title: "Password spraying", description: "Failed logins across multiple accounts from one source.", prompt: "Detect SSH password spraying: a single source trying common passwords across many accounts." },
  { title: "Distributed brute force", description: "Repeated attempts against one account from several sources.", prompt: "Detect distributed SSH brute force: repeated failed logins against one account from many source IPs." },
  { title: "Suspicious SSH activity", description: "Inspect authentication events with untrusted log content.", prompt: "Detect suspicious SSH login activity while treating all text inside log records as untrusted data." },
];

export default function Home() {
  return <PreferencesProvider><Workspace /></PreferencesProvider>;
}

function Workspace() {
  const state = useStore();
  const [view, setView] = useState<View>("home");
  const [selected, setSelected] = useState<string | null>(null);
  const [pendingId, setPendingId] = useState<string | null>(null);
  const [health, setHealth] = useState<Health>("checking");
  const [retry, setRetry] = useState(0);
  const [preferencesOpen, setPreferencesOpen] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);
  const [draftKey, setDraftKey] = useState(0);
  const [prefill, setPrefill] = useState<{ text: string; n: number } | null>(null);
  const prefillSequence = useRef(0);
  const mobileDialog = useRef<HTMLDialogElement>(null);
  const menuButton = useRef<HTMLButtonElement>(null);
  const main = useRef<HTMLElement>(null);
  const runs = useMemo(() => sortRuns(state.runs), [state.runs]);
  const skills = useMemo(() => installedSkills(state.skills, state.runs), [state.skills, state.runs]);
  const organization = useRunOrganization(runs);
  const run = selected ? state.runs[selected] : undefined;
  const awaitingFirstEvent = Boolean(pendingId && !state.runs[pendingId]?.events.length);
  const busy = awaitingFirstEvent || runs.some(r => isActive(runStatus(r)) || r.events.length === 0);
  const available = health === "ok" && state.loaded && !state.syncError && state.connection === "open";
  const notice = health === "mismatch" ? `API version mismatch. This frontend needs version ${CONTRACT_VERSION}; update the backend and retry.`
    : health === "down" ? "Cannot reach the backend. Your draft is safe; retry the connection."
    : state.syncError || (health === "ok" && state.connection === "reconnecting" ? "Connection lost. Reconnecting to load the latest events." : null);

  useEffect(() => {
    let cancelled = false;
    api.health().then(h => { if (!cancelled) setHealth(h.contract_version === CONTRACT_VERSION ? "ok" : "mismatch"); })
      .catch(() => { if (!cancelled) setHealth("down"); });
    return () => { cancelled = true; };
  }, [retry, state.connection]);

  useEffect(() => {
    const shortcut = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k" && !event.altKey && !event.shiftKey) {
        if (document.querySelector("dialog[open]")) return;
        event.preventDefault(); setHistoryOpen(true);
      }
    };
    document.addEventListener("keydown", shortcut);
    return () => document.removeEventListener("keydown", shortcut);
  }, []);

  useEffect(() => {
    const dialog = mobileDialog.current;
    if (mobileOpen && !dialog?.open) dialog?.showModal();
    if (!mobileOpen && dialog?.open) dialog.close();
  }, [mobileOpen]);

  useEffect(() => {
    const desktop = window.matchMedia("(min-width: 781px)");
    const resize = () => { if (desktop.matches) setMobileOpen(false); };
    desktop.addEventListener("change", resize);
    return () => desktop.removeEventListener("change", resize);
  }, []);

  function navigate(next: View) {
    setView(next); setMobileOpen(false);
    main.current?.focus({ preventScroll: true });
    window.scrollTo({ top: 0, behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "instant" : "smooth" });
  }
  function openRun(id: string) { setSelected(id); setPendingId(null); navigate("run"); }
  function newDetection() { setPrefill(null); setDraftKey(key => key + 1); navigate("home"); }
  function editRequest(text: string) { setDraftKey(key => key + 1); setPrefill({ text, n: ++prefillSequence.current }); navigate("home"); }
  const closePreferences = useCallback(() => setPreferencesOpen(false), []);
  const closeHistory = useCallback(() => setHistoryOpen(false), []);

  function sidebar(mobile = false) {
    return <>
      {mobile && <button className="icon-button sidebar-close" onClick={() => setMobileOpen(false)} aria-label="Close navigation"><IconX /></button>}
      <button className="brand" onClick={() => navigate("home")}><span className="brand-mark"><svg viewBox="0 0 32 32" aria-hidden="true"><path d="M8 5h17v6H14v5h9v6h-9v6H8zM3 13h5v4H3zm22 0h4v4h-4z" /></svg></span><span><span className="brand-name">Frankenstein</span></span></button>
      <button className="new-detection" onClick={newDetection}><IconPlus />New detection</button>
      <button className="history-trigger" onClick={() => { setMobileOpen(false); setHistoryOpen(true); }} aria-haspopup="dialog"><span className="history-search-mark" aria-hidden="true" /><span>Search runs</span><kbd>⌘ / Ctrl K</kbd></button>

      <nav className="primary-nav" aria-label="Main">{(["home", "run", "skills", "compare"] as View[]).map(item => <button key={item} className={`nav-item ${view === item ? "active" : ""}`} aria-current={view === item ? "page" : undefined} disabled={item === "run" && !runs.length && !pendingId} onClick={() => { if (item === "run" && !run && runs[0]) setSelected(runs[0].run_id); navigate(item); }}>
        {item === "skills" ? <IconHammer /> : item === "compare" ? <IconPanel /> : item === "run" ? <IconRefresh /> : <IconPlus />}{VIEW_NAMES[item]}{item === "skills" && <span className="nav-count">{skills.length}</span>}
      </button>)}</nav>
      <div className="sidebar-divider" /><p className="nav-label">Recent runs</p>
      <div className="recent-runs">{organization.visibleRuns.slice(0, 6).map(item => <button className="run-item history-run-item" key={item.run_id} onClick={() => openRun(item.run_id)} aria-current={view === "run" && selected === item.run_id ? "true" : undefined}>
        <span className={`run-dot ${runStatus(item) === "approved" ? "green" : runStatus(item) === "awaiting_approval" ? "amber" : ""}`} /><span className="history-run-copy"><span className="history-run-title">{organization.titleFor(item)}</span><small>{organization.metadata[item.run_id]?.pinned ? "Pinned · " : ""}{runStatus(item) ? STATUS_LABEL[runStatus(item)!] : "Loading events"}</small></span>
      </button>)}{!organization.visibleRuns.length && <p className="history-sidebar-empty">{state.loaded ? "Create a detection to see it here." : "Loading run history…"}</p>}</div>

    </>;
  }

  function recentRow(item: RunState) {
    return <tr key={item.run_id}><td><button className="recent-title" onClick={() => openRun(item.run_id)}>{organization.titleFor(item)}</button>{organization.metadata[item.run_id]?.pinned && <span className="history-pinned-label">Pinned</span>}</td><td className="mono">{item.run_id}</td><td><StatusPill status={runStatus(item)} /></td><td>{formatRelative(runCreatedAt(item)) || "—"}</td></tr>;
  }

  return <>
    <a className="skip-link" href="#main-content">Skip to content</a>
    <div className="app-shell">
      <aside className="sidebar desktop-navigation" aria-label="Navigation">{sidebar()}</aside>
      <dialog ref={mobileDialog} className="mobile-navigation" aria-label="Navigation" onCancel={() => setMobileOpen(false)} onClose={() => { setMobileOpen(false); menuButton.current?.focus(); }} onClick={e => { if (e.target === e.currentTarget && e.clientX > e.currentTarget.getBoundingClientRect().right) setMobileOpen(false); }}><div className="sidebar">{sidebar(true)}</div></dialog>
      <div className="main-shell">
        <header className="topbar"><button ref={menuButton} className="icon-button" id="menu-toggle" onClick={() => setMobileOpen(true)} aria-label="Open navigation" aria-expanded={mobileOpen}><IconSidebar /></button><div className="topbar-actions"><span className={`connection-pill ${health === "down" || state.connection === "reconnecting" ? "offline" : ""}`} role="status"><span className="status-dot" />{health === "down" ? "Backend offline" : health === "mismatch" ? "Update required" : state.connection === "open" ? "Connected" : "Connecting…"}</span><span className="topbar-divider" /><button id="preferences-toggle" className="preferences-trigger" onClick={() => setPreferencesOpen(true)} aria-haspopup="dialog"><IconPanel /><span>Preferences</span></button></div></header>
        <main className="main-content" ref={main} id="main-content" tabIndex={-1}>
          {notice && <div className="offline-banner" role="alert"><IconRefresh /><div className="offline-copy"><p>{notice}</p></div><button className="secondary-button" onClick={() => { setHealth("checking"); setRetry(n => n + 1); void refreshStore(); }}>Retry</button></div>}
          <section id="home-view" hidden={view !== "home"} aria-labelledby="home-title">
            <div className="workspace-heading"><div><h1 id="home-title">New detection</h1><p className="muted">Describe what to detect and which logs to use.</p></div></div>
            <div className="workspace-grid"><div className="workspace-primary">
              <RequestComposer key={draftKey} prefill={prefill} busy={busy} disabled={!available || view !== "home"} onCreated={id => { setSelected(id); setPendingId(id); navigate("run"); void refreshStore().then(() => setPendingId(current => current === id ? null : current)); }} />
              <section className="template-section" aria-labelledby="templates-title"><div className="section-heading"><h2 id="templates-title">Try an example</h2><span className="muted">SSH authentication</span></div><div className="suggestion-grid">{TEMPLATES.map(template => <button key={template.title} className="suggestion-card" onClick={() => setPrefill({ text: template.prompt, n: ++prefillSequence.current })}><span className="template-copy"><span className="suggestion-label">{template.title}</span><span className="suggestion-description">{template.description}</span></span><span className="template-use">Use example<IconArrowUp /></span></button>)}</div></section>
            </div><aside className="workspace-aside" aria-label="Detection context"><section className="context-section"><h2>How a run works</h2><ol className="next-steps">{[["Plan", "Identify the fields and skills needed."], ["Build & test", "Check the rule against the dataset."], ["Review", "Inspect the evidence, then approve or reject."]].map(([name, description], i) => <li key={name}><span className="next-step-index">{i + 1}</span><div><strong>{name}</strong><p>{description}</p></div></li>)}</ol></section></aside></div>
            <section className="recent-section" aria-labelledby="recent-title"><div className="section-heading"><h2 id="recent-title">Recent work</h2><button className="text-button" onClick={() => setHistoryOpen(true)}>Browse all</button></div>{organization.visibleRuns.length ? <div className="recent-table-wrap"><table className="recent-table"><thead><tr><th scope="col">Detection</th><th scope="col">Run</th><th scope="col">Status</th><th scope="col">Created</th></tr></thead><tbody>{organization.visibleRuns.slice(0, 8).map(recentRow)}</tbody></table></div> : <div className="empty-state">{state.loaded ? "No runs to show. Create a detection or find archived work in Search runs." : "Connect to the backend to load your runs."}</div>}</section>
          </section>
          {view === "run" && (run ? <DetectionRun key={run.run_id} run={run} title={organization.titleFor(run)} onEdit={editRequest} busy={busy} /> : <div className="empty-state" role="status"><h2>{pendingId ? "Waiting for the first event" : "Run unavailable"}</h2><p>{pendingId ? "Request accepted. Waiting for the backend to send events." : "This run is no longer available. Choose another run from history."}</p></div>)}
          {view === "skills" && <section id="skills-view" aria-labelledby="skills-title"><div className="page-heading"><h1 id="skills-title">Skill library</h1><p className="muted">Tools available for your detection rules.</p></div>{skills.length ? <div className="skills-grid">{skills.map(skill => <article className="panel skill-card" key={skill.name}><h2>{skill.name}</h2><p>{skill.description}</p><footer><span>{skill.kind} · {skill.origin === "seed" ? "Built-in" : "Custom"} · v{skill.version}</span></footer></article>)}</div> : <div className="empty-state">{state.loaded ? "No tools yet. Approve a detection to add its new tools." : "Connect to the backend to load the skill registry."}</div>}</section>}
          {view === "compare" && <section id="compare-view" aria-labelledby="compare-title"><div className="page-heading"><h1 id="compare-title">Compare runs</h1><p className="muted">Time, usage, and tools for the last two completed runs.</p></div><div className="panel comparison-table-wrap"><Compare runs={runs} /></div></section>}
        </main>

      </div>
    </div>
    <PreferencesDialog open={preferencesOpen} onClose={closePreferences} />
    <RunHistoryDialog open={historyOpen} onClose={closeHistory} runs={runs} organization={organization} onSelect={openRun} />
  </>;
}
