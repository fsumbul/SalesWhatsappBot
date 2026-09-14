"use client";
import { useLayoutEffect, useRef } from "react";
import { ContinuityTrack } from "./continuity";
import { reducedMotion } from "./index";

/** Natural content is measured independently of the animated outer extent.
 * Polling an unchanged size is a no-op; retargeting preserves the current track. */
export function useMotionExtent(open = true) {
  const outer = useRef<HTMLDivElement>(null);
  const inner = useRef<HTMLDivElement>(null);
  const track = useRef<ContinuityTrack | null>(null);
  const target = useRef<number | null>(null);
  const width = useRef<number | null>(null);
  useLayoutEffect(() => {
    const box = outer.current,
      content = inner.current;
    if (!box || !content || typeof ResizeObserver === "undefined") return;
    let frame: number | null = null;
    const stop = () => {
      if (frame !== null) cancelAnimationFrame(frame);
      frame = null;
    };
    const finish = () => {
      stop();
      if (target.current !== null) track.current?.snap([target.current], performance.now() / 1000);
      box.style.height = open ? "auto" : "0px";
      box.removeAttribute("data-motion-resizing");
    };
    const measure = () => {
      const rect = content.getBoundingClientRect();
      const next = open ? rect.height : 0;
      const resized = width.current !== null && Math.abs(width.current - rect.width) > 0.5;
      width.current = rect.width;
      if (track.current === null) {
        track.current = new ContinuityTrack([next]);
        target.current = next;
        finish();
        return;
      }
      if (target.current !== null && Math.abs(next - target.current) < 0.5 && !resized) return;
      target.current = next;
      if (resized || reducedMotion() || document.hidden) {
        finish();
        return;
      }
      const now = performance.now() / 1000;
      const seconds =
        parseFloat(getComputedStyle(box).getPropertyValue("--motion-layout")) / 1000 || 0.28;
      track.current.target([next], now, seconds);
      stop();
      box.setAttribute("data-motion-resizing", "");
      const render = (ms: number) => {
        const time = ms / 1000;
        box.style.height = `${Math.max(0, track.current!.sample(time)[0].p)}px`;
        if (track.current!.done(time)) {
          finish();
          return;
        }
        frame = requestAnimationFrame(render);
      };
      render(now * 1000);
    };
    const media = window.matchMedia("(prefers-reduced-motion: reduce)");
    const policy = () => {
      if (media.matches || document.hidden) finish();
    };
    const observer = new ResizeObserver(measure);
    observer.observe(content);
    measure();
    media.addEventListener("change", policy);
    document.addEventListener("visibilitychange", policy);
    return () => {
      stop();
      observer.disconnect();
      media.removeEventListener("change", policy);
      document.removeEventListener("visibilitychange", policy);
    };
  }, [open]);
  return { outer, inner };
}
