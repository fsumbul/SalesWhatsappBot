"use client";
import { useId, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { ChevronDown } from "lucide-react";
import { useMotionExtent } from "./extent";

export function MotionRegion({ children, className }: { children: ReactNode; className?: string }) {
  const { outer, inner } = useMotionExtent();
  return (
    <div ref={outer} className="motion-extent">
      <div ref={inner} className={className}>
        {children}
      </div>
    </div>
  );
}

export function MotionDisclosure({
  title,
  children,
  defaultOpen = false,
}: {
  title: ReactNode;
  children: ReactNode;
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const id = useId();
  const trigger = useRef<HTMLButtonElement>(null);
  const { outer, inner } = useMotionExtent(open);
  useLayoutEffect(() => {
    const content = inner.current;
    if (!content) return;
    if (!open && content.contains(document.activeElement)) trigger.current?.focus();
    content.toggleAttribute("inert", !open);
  }, [open, inner]);
  return (
    <div className="motion-disclosure">
      <button
        ref={trigger}
        type="button"
        className="motion-disclosure-trigger"
        aria-expanded={open}
        aria-controls={id}
        onClick={() => setOpen((value) => !value)}
      >
        <span>{title}</span>
        <ChevronDown size={16} aria-hidden="true" />
      </button>
      <div ref={outer} className="motion-extent" style={!open ? { height: 0 } : undefined}>
        <div ref={inner} id={id} aria-hidden={!open} className="motion-disclosure-content">
          {children}
        </div>
      </div>
    </div>
  );
}
