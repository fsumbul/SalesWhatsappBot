/** Adapted from Hemshare web-continuity/v1. Analytic C2 joins for autonomous
 * scalar channels; resizing/reduced-motion/background changes are explicit snaps.
 * Units: seconds and normalized panel travel. */
export type State = { p: number; v: number; a: number };
type Segment = { start: number; duration: number; from: State; target: number; c: number[] };
const finite = (n: number) => { if (!Number.isFinite(n)) throw new Error('Non-finite motion state'); return n; };
function segment(from: State, target: number, start: number, duration: number): Segment {
  [from.p, from.v, from.a, target, start, duration].forEach(finite);
  if (duration <= 0) throw new Error('Motion duration must be positive');
  const c0 = from.p, c1 = from.v*duration, c2 = from.a*duration*duration/2;
  const d = target-c0;
  return { from, target, start, duration, c: [c0,c1,c2,10*d-6*c1-3*c2,-15*d+8*c1+3*c2,6*d-3*c1-c2] };
}
function sample(s: Segment, time: number): State {
  finite(time);
  if (time <= s.start) return { ...s.from };
  if (time >= s.start+s.duration) return { p:s.target, v:0, a:0 };
  const u=(time-s.start)/s.duration, c=s.c;
  return {
    p: c[0]+u*(c[1]+u*(c[2]+u*(c[3]+u*(c[4]+u*c[5])))),
    v: (c[1]+u*(2*c[2]+u*(3*c[3]+u*(4*c[4]+u*5*c[5]))))/s.duration,
    a: (2*c[2]+u*(6*c[3]+u*(12*c[4]+u*20*c[5])))/(s.duration*s.duration),
  };
}
export class ContinuityTrack {
  private segments: Segment[];
  private lastTargetTime = -Infinity;
  constructor(initial: readonly number[]) {
    if (!initial.length) throw new Error('Motion needs at least one channel');
    this.segments=initial.map(p=>segment({p,v:0,a:0},p,0,1));
  }
  sample(time: number): State[] { return this.segments.map(s=>sample(s,time)); }
  done(time: number) { return this.segments.every(s=>time>=s.start+s.duration); }
  target(values: readonly number[], time: number, duration: number) {
    if (values.length!==this.segments.length) throw new Error('Motion channel count changed');
    if (time<this.lastTargetTime) throw new Error('Retarget timestamps must be monotonic');
    const before=this.sample(time);
    const next=values.map((p,i)=>segment(before[i],p,time,duration));
    this.segments=next; this.lastTargetTime=time;
    // Same-time event evidence: errors are per channel, never mixed across units.
    const after=this.sample(time);
    return before.map((s,i)=>({position:Math.abs(s.p-after[i].p),velocity:Math.abs(s.v-after[i].v),acceleration:Math.abs(s.a-after[i].a)}));
  }
  snap(values: readonly number[], time: number) {
    if(values.length!==this.segments.length) throw new Error('Motion channel count changed');
    finite(time);
    this.segments=values.map(p=>({start:time,duration:0,from:{p:finite(p),v:0,a:0},target:p,c:[p,0,0,0,0,0]}));
    this.lastTargetTime=time;
  }
}
