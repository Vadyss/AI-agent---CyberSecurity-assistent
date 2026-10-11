// Copyright (c) 2026 Adam Krúpa and Ondra Csajka. MIT License.
"use client";

import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";

interface RecognitionResult {
  readonly isFinal: boolean;
  readonly length: number;
  readonly [index: number]: { readonly transcript: string };
}

interface RecognitionResultEvent {
  readonly results: ArrayLike<RecognitionResult>;
}

interface RecognitionErrorEvent {
  readonly error: string;
}

interface Recognition {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  maxAlternatives: number;
  onstart: (() => void) | null;
  onend: (() => void) | null;
  onresult: ((event: RecognitionResultEvent) => void) | null;
  onerror: ((event: RecognitionErrorEvent) => void) | null;
  start(): void;
  stop(): void;
  abort(): void;
}

type RecognitionConstructor = new () => Recognition;
type RecognitionWindow = Window & {
  SpeechRecognition?: RecognitionConstructor;
  webkitSpeechRecognition?: RecognitionConstructor;
};

interface DictationSession {
  recognition: Recognition;
  baseText: string;
  stopping: boolean;
}

interface DictationOptions {
  text: string;
  onTextChange: (text: string) => void;
}

type DictationPhase = "idle" | "starting" | "listening" | "stopping";

function recognitionConstructor(): RecognitionConstructor | undefined {
  if (typeof window === "undefined") return undefined;
  const browser = window as RecognitionWindow;
  return browser.SpeechRecognition ?? browser.webkitSpeechRecognition;
}

const subscribeToSupport = () => () => {};
const getSupported = () => Boolean(recognitionConstructor());
const getServerSupported = (): null => null;

function detach(recognition: Recognition): void {
  recognition.onstart = null;
  recognition.onend = null;
  recognition.onresult = null;
  recognition.onerror = null;
}

function abort(recognition: Recognition): void {
  detach(recognition);
  try {
    recognition.abort();
  } catch {
    // An already ended recognizer can reject abort. Its callbacks are detached.
  }
}

function appendTranscript(baseText: string, transcript: string): string {
  if (!transcript) return baseText;
  const separator = baseText && !/\s$/.test(baseText) ? " " : "";
  return `${baseText}${separator}${transcript}`;
}

function recognitionError(code: string): string {
  switch (code) {
    case "not-allowed":
      return "Microphone access was blocked. Allow microphone access in your browser and try again.";
    case "service-not-allowed":
      return "Speech recognition is unavailable or blocked by your browser. Check permissions and try again.";
    case "audio-capture":
      return "Your microphone is unavailable. Check that it is connected and not being used by another app.";
    case "no-speech":
      return "No speech was detected. Try again and speak near your microphone.";
    case "network":
      return "Speech recognition could not connect. Check your internet connection and try again.";
    case "language-not-supported":
      return "English dictation is not supported by your browser.";
    case "aborted":
      return "Dictation was interrupted. Your text has been kept.";
    default:
      return "Speech recognition failed. Your text has been kept. Try again.";
  }
}

function startError(cause: unknown): string {
  const name = cause instanceof Error ? cause.name : "";
  if (name === "NotAllowedError" || name === "SecurityError") {
    return recognitionError("not-allowed");
  }
  if (name === "NotFoundError" || name === "NotReadableError") {
    return recognitionError("audio-capture");
  }
  return "Speech recognition could not start. Your text has been kept. Try again.";
}

/**
 * Call start only from a user action. Before editing, submitting, or replacing
 * the prompt, call cancel: it stops callbacks without changing the visible text.
 */
export function useDictation({ text, onTextChange }: DictationOptions) {
  const supported = useSyncExternalStore<boolean | null>(
    subscribeToSupport,
    getSupported,
    getServerSupported,
  );
  const [phase, setPhase] = useState<DictationPhase>("idle");
  const [error, setError] = useState<string | null>(null);
  const sessionRef = useRef<DictationSession | null>(null);
  const onTextChangeRef = useRef(onTextChange);

  useEffect(() => {
    onTextChangeRef.current = onTextChange;
  }, [onTextChange]);

  const finish = useCallback((session: DictationSession) => {
    if (sessionRef.current !== session) return;
    sessionRef.current = null;
    detach(session.recognition);
    setPhase("idle");
  }, []);

  const cancel = useCallback(() => {
    const session = sessionRef.current;
    // Invalidate before abort; a browser may deliver callbacks synchronously.
    sessionRef.current = null;
    if (session) abort(session.recognition);
    setPhase("idle");
  }, []);

  useEffect(() => {
    return () => {
      const session = sessionRef.current;
      sessionRef.current = null;
      if (session) abort(session.recognition);
    };
  }, []);

  const start = useCallback(() => {
    if (sessionRef.current) return;
    const Constructor = recognitionConstructor();
    if (!Constructor) {
      setError("Microphone dictation is not supported by this browser. Try Chrome, Edge, or Safari.");
      return;
    }
    if (!window.isSecureContext) {
      setError("Microphone dictation requires HTTPS or localhost.");
      return;
    }

    setError(null);
    let session: DictationSession | null = null;
    try {
      const recognition = new Constructor();
      session = { recognition, baseText: text, stopping: false };
      const activeSession = session;
      sessionRef.current = activeSession;
      recognition.lang = "en-US";
      recognition.continuous = true;
      recognition.interimResults = true;
      recognition.maxAlternatives = 1;

      recognition.onstart = () => {
        if (sessionRef.current !== activeSession) return;
        if (activeSession.stopping) {
          try {
            recognition.stop();
          } catch {
            setError("Dictation could not stop. Your text has been kept.");
            finish(activeSession);
            abort(recognition);
          }
        } else {
          setPhase("listening");
        }
      };

      recognition.onresult = (event) => {
        if (sessionRef.current !== activeSession) return;
        // results contains all final segments and the current interim segments.
        // Rebuilding from this snapshot replaces interim text without duplication.
        const segments: string[] = [];
        for (let index = 0; index < event.results.length; index += 1) {
          const transcript = event.results[index]?.[0]?.transcript.trim();
          if (transcript) segments.push(transcript);
        }
        onTextChangeRef.current(appendTranscript(activeSession.baseText, segments.join(" ")));
      };

      recognition.onerror = (event) => {
        if (sessionRef.current !== activeSession) return;
        setError(recognitionError(event.error));
        finish(activeSession);
        abort(recognition);
      };
      recognition.onend = () => finish(activeSession);

      setPhase("starting");
      recognition.start();
    } catch (cause) {
      if (session) {
        finish(session);
        abort(session.recognition);
      }
      setError(startError(cause));
    }
  }, [finish, text]);

  const stop = useCallback(() => {
    const session = sessionRef.current;
    if (!session || session.stopping) return;
    session.stopping = true;
    setPhase("stopping");
    try {
      // Keep result callbacks attached until end so the final result can arrive.
      session.recognition.stop();
    } catch {
      setError("Dictation could not stop. Your text has been kept.");
      finish(session);
      abort(session.recognition);
    }
  }, [finish]);

  const clearError = useCallback(() => setError(null), []);

  return {
    supported,
    listening: phase === "listening",
    starting: phase === "starting",
    stopping: phase === "stopping",
    error,
    start,
    stop,
    cancel,
    clearError,
  };
}
