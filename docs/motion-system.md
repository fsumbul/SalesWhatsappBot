# Ashira central motion contract

Foundation: Hemshare `docs/motion-system.md`, `docs/motion-continuity-formalization.md`, and `experiments/hemshare-design/src/motion/README.md` / `continuity.ts`. The specific Apple-centred research document has not yet been located; this implementation does not claim Apple's private timings or physics parameters. The user requested using Hemshare as the foundation, without the 12 Principles skill.

## State → dynamics → rendering

- Buttons and form controls: resting/hovered/pressed/focused. Immediate semantic state, short reversible visual feedback. Shared feedback duration and pressed scale.
- New messages and workflow surfaces: same DOM identity remains through refresh; arrival runs at mount, not each revision/poll. Small translation and opacity settle to rest. Restored history message IDs skip arrival motion. Confirming an optimistic user message does not play a second arrival.
- History drawer: one native dialog and one persistent analytic continuity track. Target changes sample position, velocity and acceleration at event time and preserve those quantities at the new segment's join. This replaces restarting a CSS/WAAPI timeline. The scalar coordinate is appropriate to this horizontal panel. No drag interaction is claimed.
- Reduced motion, window resizing, backgrounding: explicit snaps to the semantic target. Dialog retains focus trapping until closed; Escape uses the same close path. RAF and listeners are disposed on unmount.
- Loading: low amplitude opacity pulse, no fabricated progress. Reduced motion keeps static status text and dot.
- Form feedback and composer focus: optical state only, no geometry movement.

## Ownership

`apps/web/src/app/motion.css` owns shared CSS tokens, keyframes and motion-reduction policy. `apps/web/src/lib/motion/continuity.ts` contains the analytic model adapted from Hemshare. `index.ts` owns the native-dialog lifecycle and scroll preference helper. Components opt in with `data-motion="arrive|surface|feedback|waiting"`. CSS modules reuse tokens rather than assigning independent durations.

Current Ashira tuning: feedback 120ms, arrival 220ms, panel 260ms, layout 280ms, exit 180ms, travel 6px, press scale .98. These are local product choices, not measured Apple constants. Simulator transitions and waiting indicators use the same central vocabulary.

Do not attach revision IDs as component keys to restart an animation. Do not animate outgoing records into unrelated incoming records. Do not use blur to conceal a layout jump. No animation should postpone API submission or validation. Never add `transition: all`.

## Verification

Node tests cover analytic retarget continuity, 60/90/120Hz and dropped-frame sampling, interrupted dialog close/reopen, native modal lifetime, reduced-motion scrolling, policy changes and RAF disposal. These do not prove perceptual smoothness or GPU performance. Browser verification checks actual dialog opening, Escape closing and focus restoration; production build checks integration.

## Deployment 2026-09-14

Web build `8VeBfHDw17z8k6_Po1--4`, backup `C:\sites\ashiraai\backups\motion-20260914-192047`. Built against a read-only production source snapshot with only motion changes overlaid; concurrent task-result work in the shared checkout was preserved and excluded. All source snapshot hashes guarded the build switch. Private environment unchanged. Twelve motion/request-recovery tests passed. Production runtime preflight exit 0 and public `/tr` HTTP 200. Native dialog and focus verified in isolated local browser; final live browser navigation timed out, so live animation was not visually reverified.


## Extent and disclosure continuation

`useMotionExtent` observes natural content separately from its animated outer height. Initial mount uses intrinsic height. Only a changed target starts a transition; unchanged polling is a no-op. Height retargets use the same C2 continuity track. Width changes, reduced motion and backgrounding snap explicitly; settled open regions return to `height:auto`. The rendered nonnegative height clamp is outside the analytic C2 guarantee.

`MotionRegion` wraps workflow content so compose/review/result transitions share an outer surface. `MotionDisclosure` is used for record details with an actual button, aria-expanded/controls, immediate inert and aria-hidden changes, and focus return when needed. Detail content remains mounted, preserving its identity and local state. Components without ResizeObserver still expose correct open/closed states without animation.

Chat follows the bottom only while the reader is within 96px of it or explicitly sends/opens a conversation. Scrolling older content prevents response-driven forced scrolling. Restored message IDs suppress decorative entrance effects.

Ten motion tests now cover height target changes, same-size observations, midflight reversal, width reset, reduction and disposal as well as the existing dialog/trajectory contracts. Six request-recovery tests also pass. Browser gallery verified disclosure expansion/collapse, readable content and focus remaining on its trigger. Existing send actions were not invoked during this QA.

Continuation deployed as build `SSZUSyn7ChQY4z-JpshVG`, backup `C:\sites\ashiraai\backups\motion-20260914-193215`. Sixteen tests passed, isolated production build passed, full runtime preflight exit 0, guarded source/build replacement and unchanged private environment verified. Live browser opened the history drawer and restored a previously cancelled test workflow without invoking send actions; both restored message elements had no arrival attribute. Workflow details expansion verified live. Concurrent task-result code remained untouched in the workspace and excluded from this release.
