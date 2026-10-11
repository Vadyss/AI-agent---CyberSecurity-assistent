// Copyright (c) 2026 Adam Krúpa and Ondra Csajka. MIT License.
"use client";

import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { api, ApiError } from "@/lib/api";
import { useDictation } from "@/lib/useDictation";
import { usePreferences } from "@/lib/preferences";
import {
  assembleRequest, characterCount, MAX_CONTEXT_FILES, MAX_CONTEXT_FILE_BYTES,
  MAX_REQUEST_CHARACTERS, type RequestFile,
} from "@/lib/requestContext";
import { IconArrowUp, IconMic, IconStop, IconX } from "./icons";

type ComposerProps = {
  busy: boolean;
  disabled?: boolean;
  onCreated: (id: string) => void;
  prefill?: { text: string; n: number } | null;
};

function ContextIcon({ kind }: { kind: "file" | "attach" | "instructions" }) {
  const paths = {
    file: "M14 2H5v20h14V7zM14 2v6h5M8 12h8M8 16h8",
    attach: "m21 11-8 8a6 6 0 0 1-8.5-8.5l8-8a4 4 0 0 1 5.7 5.7l-8 8a2 2 0 0 1-2.8-2.8L15 6",
    instructions: "M5 4h14M5 12h14M5 20h14M9 2v4M15 10v4M9 18v4",
  };
  return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={paths[kind]} /></svg>;
}

function fileSize(size: number): string {
  return size < 1024 ? `${size} B` : `${Math.ceil(size / 1024)} KB`;
}

export function RequestComposer({ busy, disabled = false, onCreated, prefill }: ComposerProps) {
  const { preferences } = usePreferences();
  const [text, setText] = useState(prefill?.text ?? "");
  const [lastPrefill, setLastPrefill] = useState(prefill);
  const [instructions, setInstructions] = useState("");
  const [instructionDraft, setInstructionDraft] = useState("");
  const [files, setFiles] = useState<RequestFile[]>([]);
  const [preview, setPreview] = useState<RequestFile | null>(null);
  const [sending, setSending] = useState(false);
  const [reading, setReading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [fileFeedback, setFileFeedback] = useState("");
  const [fileError, setFileError] = useState(false);
  const textRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const instructionsRef = useRef<HTMLDialogElement>(null);
  const previewRef = useRef<HTMLDialogElement>(null);
  const sendingRef = useRef(false);
  const readingRef = useRef(false);
  const nextFileId = useRef(1);
  const mountedRef = useRef(true);
  const dictation = useDictation({ text, onTextChange: setText });
  const { cancel: cancelDictation } = dictation;
  const dictating = dictation.starting || dictation.listening || dictation.stopping;
  const assembled = assembleRequest(text, instructions, files);
  const count = characterCount(assembled);
  const tooLong = count > MAX_REQUEST_CHARACTERS;
  const locked = sending || disabled || busy;
  const canSubmit = !locked && !reading && !dictating && !!text.trim() && !tooLong;

  if (prefill !== lastPrefill) {
    setLastPrefill(prefill);
    if (prefill) {
      setText(prefill.text);
      setError(null);
    }
  }

  useEffect(() => {
    mountedRef.current = true;
    return () => { mountedRef.current = false; };
  }, []);

  useEffect(() => {
    if (prefill) {
      cancelDictation();
      textRef.current?.focus();
    }
  }, [prefill, cancelDictation]);

  useEffect(() => {
    if (disabled || busy) cancelDictation();
  }, [disabled, busy, cancelDictation]);

  function openInstructions() {
    setInstructionDraft(instructions);
    instructionsRef.current?.showModal();
  }

  function openFile(file: RequestFile) {
    setPreview(file);
    previewRef.current?.showModal();
  }

  function removeFile(id: number) {
    setFiles((current) => current.filter((file) => file.id !== id));
    setFileFeedback("File removed.");
    setFileError(false);
  }

  async function attachFiles(selected: File[]) {
    if (readingRef.current || sendingRef.current || !selected.length) return;
    readingRef.current = true;
    setReading(true);
    setFileFeedback("Reading files…");
    setFileError(false);
    const added: RequestFile[] = [];
    const errors: string[] = [];
    try {
      for (const file of selected) {
        if (!mountedRef.current) return;
        if (files.length + added.length >= MAX_CONTEXT_FILES) {
          errors.push("Only 3 files can be attached. Remove a file to add another.");
          break;
        }
        if (!/\.(txt|log|csv|json|md)$/i.test(file.name)) {
          errors.push(`${file.name}: choose a TXT, LOG, CSV, JSON, or MD file.`);
          continue;
        }
        if (file.size > MAX_CONTEXT_FILE_BYTES) {
          errors.push(`${file.name}: the limit is 256 KB per file.`);
          continue;
        }
        try {
          const contents = await file.text();
          if (!mountedRef.current) return;
          if (contents.includes("\0")) {
            errors.push(`${file.name}: this does not appear to be a text file.`);
            continue;
          }
          added.push({ id: nextFileId.current++, name: file.name, text: contents, size: file.size });
        } catch {
          errors.push(`${file.name}: this file could not be read. Try selecting it again.`);
        }
      }
      if (!mountedRef.current) return;
      setFiles((current) => [...current, ...added]);
      const success = added.length ? `${added.length} ${added.length === 1 ? "file" : "files"} attached.` : "";
      setFileFeedback([success, ...errors].filter(Boolean).join(" "));
      setFileError(errors.length > 0);
    } finally {
      readingRef.current = false;
      if (mountedRef.current) setReading(false);
    }
  }

  async function submit(event?: FormEvent) {
    event?.preventDefault();
    if (sendingRef.current || readingRef.current || !canSubmit) return;
    sendingRef.current = true;
    cancelDictation();
    setSending(true);
    setError(null);
    try {
      const result = await api.createRun(assembled);
      if (typeof result?.run_id !== "string" || !result.run_id) {
        throw new Error("The backend returned an invalid run response.");
      }
      if (!mountedRef.current) return;
      setText("");
      setInstructions("");
      setFiles([]);
      setFileFeedback("");
      onCreated(result.run_id);
    } catch (cause) {
      if (mountedRef.current) {
        setError(cause instanceof ApiError ? cause.message : "Could not create the run. Your draft has been kept.");
      }
    } finally {
      sendingRef.current = false;
      if (mountedRef.current) setSending(false);
    }
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key !== "Enter" || event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229) return;
    const shortcut = event.ctrlKey || event.metaKey;
    if (!event.shiftKey && !event.altKey && (shortcut || preferences.enterBehavior === "send")) {
      event.preventDefault();
      void submit();
    }
  }

  function toggleVoice() {
    if (dictation.starting) cancelDictation();
    else if (dictation.listening) dictation.stop();
    else {
      setError(null);
      dictation.start();
      textRef.current?.focus();
    }
  }

  return (
    <>
      <form className="composer-card" id="prompt-form" onSubmit={submit} aria-busy={sending}>
        <div className="composer-top"><label className="section-kicker" htmlFor="prompt-input">What do you want to detect?</label></div>
        <textarea
          id="prompt-input" ref={textRef} rows={5} value={text} readOnly={sending}
          spellCheck={preferences.spellcheck} onKeyDown={onKeyDown}
          onChange={(event) => { cancelDictation(); dictation.clearError(); setText(event.target.value); setError(null); }}
          placeholder="Detect repeated failed SSH logins from one IP across several accounts within five minutes."
          aria-describedby={`composer-note request-count${preferences.showShortcuts ? " prompt-hint" : ""}${tooLong ? " request-limit" : ""}`}
          aria-invalid={tooLong}
        />
        {(files.length > 0 || instructions || fileFeedback || error || dictation.error || tooLong) && <div className="composer-context-status">
          {(files.length > 0 || instructions) && <>
            <ul className="context-chips" aria-label="Request context">
              {files.map((file) => <li className="context-chip" key={file.id}>
                <button type="button" className="context-chip-preview" onClick={() => openFile(file)} aria-haspopup="dialog" aria-controls="context-file-dialog" aria-label={`Preview ${file.name}, ${fileSize(file.size)}`}><ContextIcon kind="file" /><span>{file.name}</span></button>
                <button type="button" className="context-chip-remove" disabled={sending || reading} onClick={() => removeFile(file.id)} aria-label={`Remove ${file.name}`}><IconX /></button>
              </li>)}
              {instructions && <li className="context-chip">
                <button type="button" className="context-chip-preview" onClick={openInstructions} aria-haspopup="dialog" aria-controls="instructions-dialog"><ContextIcon kind="instructions" /><span>Instructions</span></button>
                <button type="button" className="context-chip-remove" disabled={sending} onClick={() => setInstructions("")} aria-label="Remove instructions"><IconX /></button>
              </li>}
            </ul>
            <p className="context-local-note">Instructions and file text are sent with this request. Unsent context is not saved.</p>
          </>}
          {fileFeedback && <p className={`context-feedback${fileError ? " is-error" : ""}`} role="status">{fileFeedback}</p>}
          {tooLong && <p id="request-limit" className="context-feedback is-error" role="status">Remove {count - MAX_REQUEST_CHARACTERS} characters from the request, instructions, or attached files. The combined limit is 2,000 characters, including context labels.</p>}
          {error && <p className="context-feedback is-error" role="alert">{error}</p>}
          {dictation.error && <p className="context-feedback is-error" role="alert">{dictation.error}</p>}
        </div>}
        <div className="composer-toolbar">
          <div className="composer-context-tools">
            <button type="button" className="context-tool-button" disabled={sending || reading} onClick={() => fileInputRef.current?.click()} title="Up to 3 text files, 256 KB each; combined request limit: 2,000 characters"><ContextIcon kind="attach" /><span>{reading ? "Reading files…" : "Attach files"}</span></button>
            <input ref={fileInputRef} type="file" multiple accept=".txt,.log,.csv,.json,.md" hidden onChange={(event) => { const selected = Array.from(event.currentTarget.files ?? []); event.currentTarget.value = ""; void attachFiles(selected); }} />
            <button type="button" className={`context-tool-button${instructions ? " has-context" : ""}`} disabled={sending} onClick={openInstructions} aria-haspopup="dialog" aria-controls="instructions-dialog"><ContextIcon kind="instructions" /><span>Instructions</span></button>
          </div>
          {preferences.showShortcuts && <span className="input-hint" id="prompt-hint">{preferences.enterBehavior === "send" ? <><kbd>Enter</kbd> send · <kbd>Shift + Enter</kbd> new line</> : <><kbd>Enter</kbd> new line · <kbd>Ctrl / ⌘ + Enter</kbd> send</>}</span>}
          <div className="composer-actions">
            <button type="button" className="voice-button" disabled={dictation.supported !== true || locked || dictation.stopping} onClick={toggleVoice} aria-pressed={dictating} aria-label={dictating ? "Stop English dictation" : "Start English dictation"} title={dictating ? "Stop dictation" : "Dictate in English"}>{dictating ? <IconStop /> : <IconMic />}<span>{dictating ? "Stop dictation" : "Dictate"}</span></button>
            <button className="primary-button" type="submit" disabled={!canSubmit}>{sending ? "Starting…" : "Build detection"}<IconArrowUp /></button>
          </div>
        </div>
      </form>
      <div className="composer-footnote"><span id="composer-note" role="status">{disabled ? "Connect to the backend to send a request." : busy ? "Another run is in progress. Your draft is kept here." : ""}</span><span id="request-count">{count.toLocaleString("en-US")} / 2,000</span></div>
      <div className="composer-footnote"><span role="status">{dictation.starting ? "Starting microphone…" : dictation.stopping ? "Finishing transcription…" : dictation.listening ? "Listening… stop dictation before sending." : dictation.supported === false ? "Dictation is unavailable here. Type your request instead." : ""}</span></div>

      <dialog ref={instructionsRef} className="context-dialog" id="instructions-dialog" aria-labelledby="instructions-title" aria-describedby="instructions-description">
        <div className="context-dialog-header"><div><h2 id="instructions-title">Request instructions</h2><p id="instructions-description">Add details to include with this request.</p></div><button type="button" className="icon-button context-dialog-close" onClick={() => instructionsRef.current?.close()} aria-label="Close instructions"><IconX /></button></div>
        <form className="context-instructions-form" onSubmit={(event) => { event.preventDefault(); if (sending) return; setInstructions(instructionDraft.trim()); instructionsRef.current?.close(); }}>
          <label className="field-label" htmlFor="request-instructions">Instructions</label>
          <textarea id="request-instructions" rows={6} value={instructionDraft} readOnly={sending} spellCheck={preferences.spellcheck} onChange={(event) => setInstructionDraft(event.target.value)} placeholder="Use UTC timestamps and explain the thresholds." aria-describedby="instructions-storage instructions-count" />
          <div className="context-instructions-meta"><span id="instructions-storage">Counts toward the combined 2,000-character request limit.</span><span id="instructions-count">{characterCount(instructionDraft).toLocaleString("en-US")} characters</span></div>
          <div className="context-dialog-actions"><button type="button" className="secondary-button" onClick={() => instructionsRef.current?.close()}>Cancel</button><button className="primary-button" type="submit" disabled={sending}>Apply instructions</button></div>
        </form>
      </dialog>
      <dialog ref={previewRef} className="context-dialog" id="context-file-dialog" aria-labelledby="file-preview-title" aria-describedby="file-preview-description">
        <div className="context-dialog-header"><div><h2 id="file-preview-title">{preview?.name ?? "File preview"}</h2><p id="file-preview-description">The full text below will be included when you send this request.</p></div><button type="button" className="icon-button context-dialog-close" onClick={() => previewRef.current?.close()} aria-label="Close file preview"><IconX /></button></div>
        <p className="context-file-metadata">{preview ? `${fileSize(preview.size)} · ${characterCount(preview.text).toLocaleString("en-US")} characters` : ""}</p>
        <pre className="context-file-preview" tabIndex={0} aria-label="File contents">{preview?.text || "(Empty file)"}</pre>
        <div className="context-dialog-actions"><button type="button" className="secondary-button" disabled={sending || reading} onClick={() => { if (preview) removeFile(preview.id); previewRef.current?.close(); }}>Remove file</button><button type="button" className="primary-button" onClick={() => previewRef.current?.close()}>Done</button></div>
      </dialog>
    </>
  );
}
