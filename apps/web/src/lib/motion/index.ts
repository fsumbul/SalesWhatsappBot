"use client";
import { useCallback, useEffect, useRef } from "react";
import { ContinuityTrack } from "./continuity";

export function reducedMotion() {
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}
export function motionScrollBehavior(): ScrollBehavior {
  return reducedMotion() ? "auto" : "smooth";
}

/** One persistent surface and track. Retargeting inherits position, velocity and
 * acceleration. Native dialog owns focus trapping until the exit reaches rest. */
export function useMotionDialog() {
  const ref = useRef<HTMLDialogElement>(null);
  const track = useRef(new ContinuityTrack([0]));
  const frame = useRef<number | null>(null);
  const target = useRef(0);
  const stop = useCallback(() => {
    if (frame.current !== null) cancelAnimationFrame(frame.current);
    frame.current = null;
  }, []);
  const settle = useCallback(() => {
    stop();
    track.current.snap([target.current], performance.now() / 1000);
    const el = ref.current;
    if (!el) return;
    el.style.removeProperty("transform");
    if (!target.current && el.open) el.close();
  }, [stop]);
  const move = useCallback((open: boolean) => {
    const el = ref.current;
    if (!el || (!open && !el.open)) return;
    const time = performance.now() / 1000;
    const value = open ? 1 : 0;
    if (open && !el.open) { track.current.snap([0], time); el.showModal(); }
    target.current = value;
    if (reducedMotion() || document.hidden) { settle(); return; }
    const duration = parseFloat(getComputedStyle(el).getPropertyValue(open ? "--motion-panel" : "--motion-exit")) / 1000;
    track.current.target([value], time, Number.isFinite(duration) && duration > 0 ? duration : .26);
    stop();
    const render = (ms: number) => {
      const t = ms / 1000;
      const position = track.current.sample(t)[0].p;
      el.style.transform = `translateX(${(position - 1) * 100}%)`;
      if (track.current.done(t)) { settle(); return; }
      frame.current = requestAnimationFrame(render);
    };
    render(time * 1000);
  }, [settle, stop]);
  useEffect(() => {
    const media = window.matchMedia("(prefers-reduced-motion: reduce)");
    const policy = () => { if (media.matches || document.hidden) settle(); };
    media.addEventListener("change", policy);
    document.addEventListener("visibilitychange", policy);
    window.addEventListener("resize", settle);
    return () => { stop(); media.removeEventListener("change", policy); document.removeEventListener("visibilitychange", policy); window.removeEventListener("resize", settle); };
  }, [settle, stop]);
  return { ref, open: () => move(true), close: () => move(false) };
}
