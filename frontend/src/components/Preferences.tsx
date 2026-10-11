// Copyright (c) 2026 Adam Krúpa and Ondra Csajka. MIT License.
"use client";

import { useEffect, useRef } from "react";
import { usePreferences, type PreferenceState } from "@/lib/preferences";

const themeIcons = {
  light: <><circle cx="12" cy="12" r="4" /><path d="M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1.5 1.5m11 11L19 19M5 19l1.5-1.5m11-11L19 5" /></>,
  dark: <path d="M20 14.1A8.5 8.5 0 0 1 9.9 4a8.5 8.5 0 1 0 10.2 10.2Z" />,
  system: <><rect x="3" y="4" width="18" height="13" rx="2" /><path d="M12 17v4m-4 0h8" /></>,
};

type SegmentedPreference = "readingSize" | "density" | "contentWidth";
function Segments<K extends SegmentedPreference>({ name, label, description, options }: {
  name: K;
  label: string;
  description: string;
  options: readonly { value: PreferenceState[K]; label: string }[];
}) {
  const { preferences, updatePreference } = usePreferences();
  return (
    <div className="preference-row">
      <div className="preference-copy"><strong id={`${name}-label`}>{label}</strong><p id={`${name}-help`}>{description}</p></div>
      <div className="preference-segments" role="radiogroup" aria-labelledby={`${name}-label`} aria-describedby={`${name}-help`}>
        {options.map((option) => <label key={option.value}><input type="radio" name={name} data-preference={name} value={option.value} checked={preferences[name] === option.value} onChange={() => updatePreference(name, option.value)} /><span>{option.label}</span></label>)}
      </div>
    </div>
  );
}

type BooleanPreference = { [K in keyof PreferenceState]: PreferenceState[K] extends boolean ? K : never }[keyof PreferenceState];
function Toggle({ name, label, description }: { name: BooleanPreference; label: string; description: string }) {
  const { preferences, updatePreference } = usePreferences();
  return <label className="preference-toggle" htmlFor={`preference-${name}`}><span className="preference-copy"><strong>{label}</strong><small id={`${name}-help`}>{description}</small></span><input id={`preference-${name}`} type="checkbox" data-preference={name} checked={preferences[name]} onChange={(event) => updatePreference(name, event.target.checked)} aria-describedby={`${name}-help`} /></label>;
}

export function PreferencesDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const { preferences, updatePreference, resetPreferences, storageMessage } = usePreferences();

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!open || !dialog) return;
    const trigger = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    if (!dialog.open) dialog.showModal();
    return () => {
      if (dialog.open) dialog.close();
      if (trigger?.isConnected) trigger.focus({ preventScroll: true });
    };
  }, [open]);

  return (
    <dialog ref={dialogRef} className="preferences-dialog" id="preferences-dialog" aria-labelledby="preferences-title" aria-describedby="preferences-description" onCancel={(event) => { event.preventDefault(); onClose(); }}>
      <div className="preferences-header">
        <div><h2 id="preferences-title">Preferences</h2><p id="preferences-description">Appearance and input settings.</p></div>
        <button className="icon-button" id="preferences-close" type="button" aria-label="Close preferences" onClick={onClose}><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" aria-hidden="true"><path d="m6 6 12 12M6 18 18 6" /></svg></button>
      </div>
      <form className="preferences-form" method="dialog" onSubmit={(event) => { event.preventDefault(); onClose(); }}>
        <div className="preferences-body">
          <fieldset>
            <legend>Appearance</legend>
            <p className="preferences-description">Choose a theme or follow your device.</p>
            <div className="theme-options">
              {(["light", "dark", "system"] as const).map((theme) => <label className="theme-option" key={theme}><input type="radio" name="theme" data-preference="theme" value={theme} checked={preferences.theme === theme} onChange={() => updatePreference("theme", theme)} /><span className="theme-choice"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{themeIcons[theme]}</svg>{theme[0].toUpperCase() + theme.slice(1)}</span></label>)}
            </div>
            <Segments name="readingSize" label="Reading size" description="Larger text in requests, results, and rule code." options={[{ value: "standard", label: "Standard" }, { value: "large", label: "Large" }]} />
            <Segments name="density" label="Density" description="Adjust the space between items." options={[{ value: "comfortable", label: "Comfortable" }, { value: "compact", label: "Compact" }]} />
            <Segments name="contentWidth" label="Content width" description="Use more horizontal space on larger screens." options={[{ value: "standard", label: "Standard" }, { value: "wide", label: "Wide" }]} />
          </fieldset>
          <fieldset>
            <legend>Layout</legend>
            <Toggle name="showTemplates" label="Show templates" description="Suggested requests below the prompt editor." />
            <Toggle name="showRecentWork" label="Show recent work" description="Recent runs on the start page. All runs stay available in search." />
            <Toggle name="showContext" label="Show help and run details" description="Help beside the editor and details beside each run." />
          </fieldset>
          <fieldset>
            <legend>Input</legend>
            <p className="preferences-description">Choose what the Enter key does while writing your prompt.</p>
            <div className="keyboard-options">
              <label className="keyboard-option"><input type="radio" name="enter-behavior" data-preference="enterBehavior" value="newline" checked={preferences.enterBehavior === "newline"} onChange={() => updatePreference("enterBehavior", "newline")} /><span><strong>New line</strong><small>Enter adds a line. Ctrl / Cmd + Enter sends.</small></span></label>
              <label className="keyboard-option"><input type="radio" name="enter-behavior" data-preference="enterBehavior" value="send" checked={preferences.enterBehavior === "send"} onChange={() => updatePreference("enterBehavior", "send")} /><span><strong>Send prompt</strong><small>Enter sends. Shift + Enter adds a line.</small></span></label>
            </div>
            <Toggle name="spellcheck" label="Check spelling" description="Use your browser's spellcheck in the prompt editor." />
            <Toggle name="showShortcuts" label="Show keyboard hints" description="Display sending shortcuts below the prompt. Shortcuts always work." />
          </fieldset>
          <fieldset>
            <legend>Rule viewer</legend>
            <Toggle name="wrapCode" label="Wrap long lines" description="Keep rule code within the viewer. Turn off to scroll horizontally." />
          </fieldset>
          <div className="preferences-reset-row"><p id="preferences-reset-help">Restore these settings only. Your draft, files, and session history stay as they are.</p><button className="secondary-button" id="preferences-reset" type="button" aria-describedby="preferences-reset-help" onClick={resetPreferences}>Restore defaults</button></div>
        </div>
        <div className="preferences-footer"><span id="preferences-storage" role="status">{storageMessage}</span><button className="primary-button" type="submit">Done</button></div>
      </form>
    </dialog>
  );
}
