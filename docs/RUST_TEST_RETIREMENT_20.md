# NURBS computational regression transfer to Rust

The two new mandatory compiled/executed integration suites are nurbs_golden and
arclength_golden. The source gate now requires15Rust integration executables,
their exact public source/fixture closure and unfiltered passing test evidence.
Missing host-boundary suites are still rejected by packaging tests.

## Independent expected answers and coverage

NURBS expectations were captured from original Python nearest search, display
sampling and openNURBS PointAt, not the Rust functions under test. Rust checks
5,678nearest expectations through scalar AND batch ABIs, all1,840original
display parity cases plus retry/performance inputs (2,141cases), and3,048extra
uniform PointAt sample cases. Display output covers10,371,637vertices. Exact
counts/status and a fingerprint of EVERY output bit avoid a705MB public fixture.
The fingerprint is not a cryptographic proof; original raw answers and hashes
are preserved locally. No tolerance was widened or random corpus sampled away.

Arc-length expectations come from the original CPython arclength._invert on
134curves. Rust replays5,315rows:5,312bit-exact native inversions plus3refusals,
including62random rational curves, nonuniform knots, shifted domains and128
distinct tolerances. Original metadata/null/overflow/nonfinite/nonmonotonic
and no-write cases are transferred too. We did not create a second Rust oracle.

## Python retained only where this transfer needs a host

Nearest/display tests retain all31original fixture host groups and3,096queries,
covering actual rhino3dm packing, edits/snapshots, wrapper method/ambiguity fields,
osnap routing/cache, counters, platform refusal and non-NURBS fallbacks. Arc-length
retains actual packing, document tolerance, error presentation, LineCurve/PolyCurve
fallbacks, required-fit refusal, and complete Rebuild curve/report parity.
Existing performance criteria are unchanged and still execute against the active
production fallback where it remains necessary. Their timing is not replaced by
fixture lookup speed. Large Python random/parity loops are removed, not duplicated.

## Safety and scope

No production code, kernels, geometry representation, recipes, historic files or
Undo owner changed. Still-required non-NURBS and unqualified-platform fallbacks
remain complete. Display bit parity is qualified for Linux/glibc; source checks
are NOT Windows runtime certification. Originals/generators/mutation receipts
remain locally under ignored legacy/retired-rust20. Public tests have no private
archive dependency. Mutation checks cover tie rules, libm power/arithmetic order,
inversion weights, native/fallback status and host routing.

This is a bounded computational test transfer, not full Python removal. Necessary
host checks remain; measured net source removal is recorded at publication after
including gate/policy overhead. No language-statistics hiding, release or version
change is part of this checkpoint.
