# Persistent UI ownership and interaction regression review

## Changes

- Shared text editing now uses a persistent Rust owner for text, caret, selection,
  movement and editing. Python retains Blender integration and exact font layout.
- Popup layout, navigation, selection, scrolling and normal release guards now use
  a persistent Rust owner. Python retains opaque action payloads and exact whole-string
  font callbacks. Neither path replays failed mutations into a fallback owner.
- First-use overlap selection installs its draw handler before consuming input.
  Fractional-scale rounding no longer hides a candidate behind unnecessary scroll gutters.
- Popup event/draw failures are reported and detached without terminating command input.
  Pending releases remain shielded; an action callback's replacement popup survives.
- Geometry startup actually initializes OCCT. Additive gumball extrusions no longer
  rebuild unrelated display objects; replacement extrusions refresh changed objects only.
- Recipe-backed planar polygon solids reach the certified native component-movement
  engine before the planar-only compatibility path. Warped faces remain exact B-splines,
  with analytic masters, recipes, validity and airtightness preserved.
- AI action review now displays explicit approval/rejection, cancellation, expiry,
  plan-only status and unknown outcomes. Normal Sphere/Cylinder/Cone catalogs remain
  disabled; test-only admission is not a production feature flag.
- Linux, Windows and macOS packaging includes ui_core with ABI/export/source checks.
  This is source integration, not qualification or publication of new platform packages.

## Focused evidence

Measured on Linux development runtime, before the combined integration gate:

- Gumball synchronous release: first circle 17.9 ms, circle with 96 unrelated cylinders
  13.1 ms, existing-face extrusion with 96 cylinders 27.8 ms. The last case previously
  measured 686 ms. All 96 unrelated object and mesh identities survived.
- Seven extrusion scenarios check exact geometry, recipes, volume, selection, Undo/Redo
  and actual framebuffer appearance. Restoring old warm-up/rebuild behavior causes
  real 669–710 ms regression failures; these are not injected sleeps.
- Fresh vertex/edge/face/object-first overlap selection each passed 180 visible GUI
  checks. Draw registration, highlight and fractional-scale mutations failed.
- Exact movement: eight focused tests, routing mutation RED then restored GREEN,
  fresh-process save/reopen and implicit library discovery. The reopened edited box
  contains three B-spline and three planar faces, not a faceted approximation.
- Editor: Unicode 15.1 and 16 compatibility, 6,000 state transitions, unchanged callers,
  lifetime stress and 35 mutants; actual GUI qualification includes 75 checks.
- Popup: 42 native and 24 legacy tests; 195 native / 194 legacy GUI checks.
  Failure recovery adds 14 tests, 12 source mutants and 17 actual GUI checks.
  A deliberate escaping-exception mutation reproduces loss of console typing.
- AI visible review: 163 checks, 52 loopback requests and ten analytic solids reopened
  in a fresh process. Scope/expiry injections are distinguished from actual UI events.
- ui_core source gate checks formatting, strict Clippy, Rust tests, editor ABI1/State88,
  popup ABI1 and all required exports. Its receipt binds the binary and all crate inputs.

## Final combined integration gate

All 268 registered gates passed across a full-run prefix and recorded continuation
runs, with all 3,082 frozen public files checked for drift. This was not an uninterrupted
full run. Product sources remained unchanged throughout; fixture import/setup and
read-only popup projection compatibility were corrected, and a missing implicit exact
Brep writer was restored in the isolated candidate. The original test assertions and
geometry gates were not weakened.

All seven final extrusion scenarios passed: maximum synchronous release 44.994 ms,
maximum enqueue-to-completed-viewport-draw 94.208 ms, against 250/500 ms budgets.
Final staged editor GUI passed 69 checks and popup failure recovery 17 checks; the
previous editor count of 75 included mutation checks. Native and fallback popup paths,
all four first-use chooser cases, AI approval/fresh geometry round-trip and startup
layout passed. The public-source audit reported no problems.

Staged-product automation startup acceptance remains excluded by the harness. Optional
icon/reference, converter, TraceDraw and CDR-fixture skips are not counted as passes.
These results do not establish every-platform or release-package acceptance.

## Boundaries

Native owners remove duplicate mutable Python authority; this does not mean every tiny
FFI event is faster than an in-process Python operation. Font callbacks remain exact;
general Rust text layout is still test-only. Compatibility paths remain available when
no native core is installed, selected before construction rather than after a failure.

Actual physical display/compositor latency, OS-close AI acceptance, live provider
quality, Windows runtime and macOS runtime acceptance are not established by these
Linux probes. Restart an already-running application to load new code and libraries.

Behavioral research was reviewed separately. Distinguished bent-polyline Divide modes,
option-memory observations and partial construction/editing traces inform future
acceptance cases; unmeasured branches are not treated as full command parity. In
particular, successful Loft option/style completion remains unmeasured. Private native
research captures and control tooling are not included in this public source change.

Existing released packages, version numbers and tags are unchanged.
