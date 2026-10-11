// Copyright (c) 2026 Adam Krúpa and Ondra Csajka. MIT License.
"use client";

import { createContext, useContext, useEffect, useMemo, useSyncExternalStore, type ReactNode } from "react";
import { useSmoothScroll } from "./useSmoothScroll";

export interface PreferenceState {
  theme: "light" | "dark" | "system";
  enterBehavior: "newline" | "send";
  readingSize: "standard" | "large";
  density: "comfortable" | "compact";
  contentWidth: "standard" | "wide";
  showTemplates: boolean;
  showRecentWork: boolean;
  showContext: boolean;
  spellcheck: boolean;
  showShortcuts: boolean;
  wrapCode: boolean;
}

export const defaultPreferences: Readonly<PreferenceState> = Object.freeze({
  theme: "dark", enterBehavior: "newline", readingSize: "standard",
  density: "comfortable", contentWidth: "standard", showTemplates: true,
  showRecentWork: true, showContext: true, spellcheck: true,
  showShortcuts: true, wrapCode: true,
});

const storageKey = "frankenstein-preferences";
const options: Partial<Record<keyof PreferenceState, readonly string[]>> = {
  theme: ["light", "dark", "system"], enterBehavior: ["newline", "send"],
  readingSize: ["standard", "large"], density: ["comfortable", "compact"],
  contentWidth: ["standard", "wide"],
};
const sessionMessage = "Applied for this session. Browser storage is unavailable.";
const initialSnapshot = { preferences: defaultPreferences as PreferenceState, storageMessage: "Changes apply immediately." };
let snapshot = initialSnapshot;
const listeners = new Set<() => void>();

function isValid<K extends keyof PreferenceState>(key: K, value: unknown): value is PreferenceState[K] {
  if (!Object.hasOwn(defaultPreferences, key)) return false;
  const allowed = options[key];
  return allowed ? typeof value === "string" && allowed.includes(value) : typeof value === "boolean";
}

function parsePreferences(raw: string | null): PreferenceState {
  const preferences = { ...defaultPreferences };
  if (raw === null) return preferences;
  const saved: unknown = JSON.parse(raw);
  if (!saved || typeof saved !== "object" || Array.isArray(saved)) return preferences;
  const assign = <K extends keyof PreferenceState>(key: K) => {
    const value = (saved as Record<string, unknown>)[key];
    if (isValid(key, value)) preferences[key] = value;
  };
  (Object.keys(defaultPreferences) as (keyof PreferenceState)[]).forEach(assign);
  return preferences;
}

function notify() {
  listeners.forEach((listener) => listener());
}

function loadPreferences() {
  let raw: string | null;
  try {
    raw = window.localStorage.getItem(storageKey);
  } catch {
    snapshot = { ...snapshot, storageMessage: sessionMessage };
    return;
  }
  try {
    snapshot = {
      preferences: parsePreferences(raw),
      storageMessage: raw === null ? "Changes apply immediately." : "Saved in this browser.",
    };
  } catch {
    snapshot = { preferences: { ...defaultPreferences }, storageMessage: "Invalid saved settings were reset." };
  }
}

function onStorage(event: StorageEvent) {
  if (event.key !== null && event.key !== storageKey) return;
  // Re-read our own storage so unrelated sessionStorage events cannot change settings.
  loadPreferences();
  notify();
}

function subscribe(listener: () => void) {
  const first = listeners.size === 0;
  listeners.add(listener);
  if (first) {
    loadPreferences();
    window.addEventListener("storage", onStorage);
  }
  return () => {
    listeners.delete(listener);
    if (!listeners.size) window.removeEventListener("storage", onStorage);
  };
}

function savePreferences(preferences: PreferenceState) {
  let storageMessage = "Saved in this browser.";
  try {
    window.localStorage.setItem(storageKey, JSON.stringify(preferences));
  } catch {
    storageMessage = sessionMessage;
  }
  snapshot = { preferences, storageMessage };
  notify();
}

function updatePreference<K extends keyof PreferenceState>(key: K, value: PreferenceState[K]) {
  if (isValid(key, value)) savePreferences({ ...snapshot.preferences, [key]: value });
}

function resetPreferences() {
  savePreferences({ ...defaultPreferences });
}

interface PreferencesContextValue {
  preferences: PreferenceState;
  updatePreference: typeof updatePreference;
  resetPreferences: typeof resetPreferences;
  storageMessage: string;
}
const PreferencesContext = createContext<PreferencesContextValue | null>(null);
const getSnapshot = () => snapshot;
const getServerSnapshot = () => initialSnapshot;

export function PreferencesProvider({ children }: { children: ReactNode }) {
  // The server and initial hydration render share defaults. Browser storage is
  // read when React subscribes, never during the server render.
  const state = useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
  const { preferences } = state;
  useSmoothScroll();

  useEffect(() => {
    const body = document.body;
    const systemTheme = window.matchMedia("(prefers-color-scheme: light)");
    const classSettings = {
      "hide-templates": !preferences.showTemplates,
      "hide-recent-work": !preferences.showRecentWork,
      "hide-context": !preferences.showContext,
      "hide-shortcuts": !preferences.showShortcuts,
      "code-nowrap": !preferences.wrapCode,
    };
    const classNames = ["light", ...Object.keys(classSettings)];
    const previousClasses = classNames.map((name) => [name, body.classList.contains(name)] as const);
    const dataNames = ["readingSize", "density", "contentWidth"] as const;
    const previousData = dataNames.map((name) => [name, body.dataset[name]] as const);
    const themeColor = document.querySelector<HTMLMetaElement>('meta[name="theme-color"]');
    const previousColor = themeColor?.content;

    const applyTheme = () => {
      body.classList.toggle("light", preferences.theme === "light" || (preferences.theme === "system" && systemTheme.matches));
      if (themeColor) {
        const color = getComputedStyle(body).getPropertyValue("--bg").trim();
        if (color) themeColor.content = color;
      }
    };
    Object.entries(classSettings).forEach(([name, active]) => body.classList.toggle(name, active));
    dataNames.forEach((name) => { body.dataset[name] = preferences[name]; });
    applyTheme();
    systemTheme.addEventListener("change", applyTheme);
    return () => {
      systemTheme.removeEventListener("change", applyTheme);
      previousClasses.forEach(([name, active]) => body.classList.toggle(name, active));
      previousData.forEach(([name, value]) => {
        if (value === undefined) delete body.dataset[name];
        else body.dataset[name] = value;
      });
      if (themeColor && previousColor !== undefined) themeColor.content = previousColor;
    };
  }, [preferences]);

  const value = useMemo(() => ({ ...state, updatePreference, resetPreferences }), [state]);
  return <PreferencesContext.Provider value={value}>{children}</PreferencesContext.Provider>;
}

export function usePreferences() {
  const context = useContext(PreferencesContext);
  if (!context) throw new Error("usePreferences must be used inside PreferencesProvider.");
  return context;
}
