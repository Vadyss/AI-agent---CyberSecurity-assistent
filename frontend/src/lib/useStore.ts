// Copyright (c) 2026 Adam Krúpa and Ondra Csajka. MIT License.
"use client";

import { useEffect, useSyncExternalStore } from "react";
import { Engine, INITIAL_STATE } from "./engine";

let engine: Engine | null = null;

function getEngine(): Engine {
  if (!engine) engine = new Engine();
  return engine;
}

export function refreshStore() {
  return getEngine().resync();
}

export function useStore() {
  const e = getEngine();
  useEffect(() => {
    e.start();
    return () => e.stop();
  }, [e]);
  return useSyncExternalStore(e.subscribe, e.getSnapshot, () => INITIAL_STATE);
}
