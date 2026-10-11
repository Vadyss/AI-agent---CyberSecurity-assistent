// Copyright (c) 2026 Adam Krúpa and Ondra Csajka. MIT License.
"use client";

import { useEffect } from "react";

interface ScrollAnimation {
  element: Element;
  direction: number;
  start: number;
  lastTop: number;
  target: number;
  started: number;
}

/** Ease coarse mouse-wheel steps, keeping touch, trackpads, and keys native. */
export function useSmoothScroll() {
  useEffect(() => {
    const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
    const duration = 180;
    let animation: ScrollAnimation | null = null;
    let frame = 0;

    function cancel() {
      if (frame) window.cancelAnimationFrame(frame);
      frame = 0;
      animation = null;
    }
    const limit = (element: Element) => Math.max(0, element.scrollHeight - element.clientHeight);
    const clamp = (value: number, maximum: number) => Math.max(0, Math.min(maximum, value));

    function scrollContainer(target: Element, direction: number): { element?: Element; boundary?: boolean } {
      const root = document.scrollingElement;
      const modal = target.closest("dialog[open]");
      if (document.querySelector("dialog[open]") && !modal) return { boundary: true };
      for (let element: Element | null = target; element; element = element.parentElement) {
        const style = window.getComputedStyle(element);
        if (element === root && !modal) {
          const bodyStyle = window.getComputedStyle(document.body);
          if (/hidden|clip/.test(style.overflowY) || /hidden|clip/.test(bodyStyle.overflowY)) return { boundary: true };
        }
        const scrollable = element === root || /^(auto|scroll)$/.test(style.overflowY);
        const maximum = scrollable ? limit(element) : 0;
        if (maximum > 1 && (direction > 0 ? element.scrollTop < maximum - 1 : element.scrollTop > 1)) return { element };
        if (scrollable && /^(contain|none)$/.test(style.overscrollBehaviorY)) return { boundary: true };
        if (element === modal) return { boundary: true };
      }
      return {};
    }

    function tick(time: number) {
      frame = 0;
      const current = animation;
      if (!current) return;
      const element = current.element;
      if (!element.isConnected || document.hidden || reducedMotion.matches || Math.abs(element.scrollTop - current.lastTop) > 2) {
        cancel();
        return;
      }
      const maximum = limit(element);
      current.target = clamp(current.target, maximum);
      const progress = Math.min(1, (time - current.started) / duration);
      const eased = 1 - Math.pow(1 - progress, 3);
      const top = clamp(current.start + (current.target - current.start) * eased, maximum);
      element.scrollTo({ top, behavior: "instant" });
      current.lastTop = element.scrollTop;
      if (progress < 1 && Math.abs(current.target - current.lastTop) > 0.5) frame = window.requestAnimationFrame(tick);
      else cancel();
    }

    function onWheel(event: WheelEvent) {
      const target = event.target instanceof Element ? event.target : event.target instanceof Node ? event.target.parentElement : null;
      if (!target) return;
      // Input devices are not identified by the browser. Preserve small and
      // fractional pixel deltas so normal trackpad inertia stays native.
      const coarse = event.deltaMode !== WheelEvent.DOM_DELTA_PIXEL || (Math.abs(event.deltaY) >= 50 && Number.isInteger(event.deltaY));
      if (event.defaultPrevented || !event.cancelable || reducedMotion.matches || !coarse || !event.deltaY || event.deltaX
        || event.ctrlKey || event.metaKey || event.shiftKey || target.closest("select, input[type='number'], input[type='range'], [role='listbox']")) {
        cancel();
        return;
      }
      const direction = Math.sign(event.deltaY);
      const destination = scrollContainer(target, direction);
      if (!destination.element) {
        cancel();
        if (destination.boundary) event.preventDefault();
        return;
      }
      const element = destination.element;
      const lineHeight = Number.parseFloat(window.getComputedStyle(element).lineHeight) || 20;
      const unit = event.deltaMode === WheelEvent.DOM_DELTA_LINE ? lineHeight : event.deltaMode === WheelEvent.DOM_DELTA_PAGE ? element.clientHeight * 0.9 : 1;
      const currentTop = element.scrollTop;
      const continuing = animation?.element === element && animation.direction === direction && Math.abs(currentTop - animation.lastTop) <= 2;
      const previousTarget = continuing && animation ? animation.target : currentTop;
      const backlog = Math.max(120, Math.min(600, element.clientHeight * 0.8));
      const targetTop = clamp(Math.max(currentTop - backlog, Math.min(currentTop + backlog, previousTarget + event.deltaY * unit)), limit(element));
      if (Math.abs(targetTop - currentTop) < 0.5) return;
      event.preventDefault();
      cancel();
      animation = { element, direction, start: currentTop, lastTop: currentTop, target: targetTop, started: performance.now() };
      frame = window.requestAnimationFrame(tick);
    }

    document.addEventListener("wheel", onWheel, { passive: false });
    document.addEventListener("keydown", cancel, true);
    document.addEventListener("pointerdown", cancel, { capture: true, passive: true });
    document.addEventListener("touchstart", cancel, { capture: true, passive: true });
    document.addEventListener("visibilitychange", cancel);
    document.addEventListener("close", cancel, true);
    window.addEventListener("blur", cancel);
    window.addEventListener("resize", cancel);
    reducedMotion.addEventListener("change", cancel);
    return () => {
      cancel();
      document.removeEventListener("wheel", onWheel);
      document.removeEventListener("keydown", cancel, true);
      document.removeEventListener("pointerdown", cancel, true);
      document.removeEventListener("touchstart", cancel, true);
      document.removeEventListener("visibilitychange", cancel);
      document.removeEventListener("close", cancel, true);
      window.removeEventListener("blur", cancel);
      window.removeEventListener("resize", cancel);
      reducedMotion.removeEventListener("change", cancel);
    };
  }, []);
}
