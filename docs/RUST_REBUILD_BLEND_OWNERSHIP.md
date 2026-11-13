# Required native Rebuild and Blend mathematics

Rebuild least-squares fits and Blend endpoint control-point solves now require the
existing exact Rust implementations, at every supported size/order. Python no
longer contains the duplicate basis/normal-equation/Gaussian-elimination fit or
forward/backward endpoint solver. Original engines are privately archived with
hashes under ignored legacy/retired-rust15; public test-only references are never
loaded by the application.

## Exact geometry and scope

This is numerical ownership consolidation, not a change to manufacturing kernels.
Rebuild still creates a real openNURBS NurbsCurve with the same degree, knots and
control points, including periodic wrapping and preserved endpoint tangents.
Python retains the rhino3dm construction and existing arc-length/closest-point
orchestration because those are host/API integration. The separate native
arc-length and nearest evaluators remain in use where already qualified.

Blend still extracts exact endpoint derivatives through openNURBS and constructs
a real Bezier/NURBS curve. All25 supported continuity pairs (G0 throughG4 at each
end) use the same Rust endpoint identities. No samples or display triangles become
curve geometry. This batch does not introduce interactive handle controls or
change existing command options, exact OCCT recipes, save formats or Undo ownership.

## Required ABI and failures

Core ABI1 plus the existing stable rebuild-fit and blend-control-point exports
are required. Package probes require the symbols AND execute real numeric and
classified-refusal witnesses; an unrelated new marker is not substituted for
semantic qualification. Missing/stale libraries and malformed/native failures
are explicit, never replayed in Python. Default lookup is package-adjacent only;
a set environment path is exact, including empty strings, and never aliases the
unset packaged signature. Immutable binding records retain their library owner
through each operation. Warm cache hits do not build paths or acquire locks.

Rebuild validates counts and input spans before passing pointers, refuses more
than4096 unknowns by name, and validates control count/capacity, finite controls,
expected knots and periodic wrapping before constructing a curve. Singular fits
retain their named user-facing refusal. Blend validates supported orders and
input lengths and refuses nonfinite returned controls even if a native library
incorrectly reports success. Native invocation exceptions are named runtime
errors, not misclassified as input admission failures.

## Evidence and cost

Rebuild qualification compares native fits bit-for-bit with an independent
reference, including closed/open/tangent cases and complete rebuilt curves and
reports. On this host, a40-point/801-sample fit measured18.4ms in the oracle
versus1.12ms native, while the complete rebuild measured301ms versus176ms.
A three-point call including validation/buffers cost about26us. These numbers
are workload measurements, not guarantees for every file.

Blend tests independently reconstruct endpoint derivatives using Bernstein
identities for all25 pairs and compare400 seeded plus120 orientation/speed cases
bit-for-bit. Position and Tangency remain slower through ctypes (about0.1x and
0.6x); Curvature/G4 measured about1.2x/2.9x. Native-only ownership deliberately
removes redundant engines despite tiny-call overhead; it is not a universal
speedup claim. Fault-injection and restored RED-to-GREEN mutations cover missing
support, cache collisions, runtime classification and output admission.

The source gate cross-checks Windows GNU builds; this is not Windows runtime
certification. No release/version/download changes accompany this checkpoint.
