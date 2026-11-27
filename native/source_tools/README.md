# Native source tools

Rust owns publication selection, privacy/license admission, dependency closure,
root-history export, and the pure-core qualification driver. Build from the
locked source with `scripts/source_tools.sh`; no Python bootstrap engine is used.

## Pure-core qualification

`scripts/source_tools.sh pure-gate --json` performs formatting, mandatory clippy,
Rust tests with compiler-artifact and unfiltered execution evidence for every
integration target, release compilation, direct ABI probes, the explicit actual
SDK test manifest, and the optional Windows GNU compile check, in that order.
A skipped clippy step is a failure. All children have time and output bounds;
timed-out children and their owned process groups are terminated. No application
process is used. The Python `check_pure_core` module is a compatibility transport
for existing packaging and SDK-test imports, not an implementation of the gate.

The 19 typed ABI groups contain 411 fixed C-ABI calls. The frozen witness catalog
holds finite input/output bytes, scalar statuses, pointer relocations, and owned
handle identities. It holds no Python programs, ASTs or bytecode. Rust invokes
reviewed static C signatures, checks return values and all observed allocations,
checks allocation guard bytes, and tracks live owned results through release.
Independent real-SDK tests still compare SDK-produced records and numerical
results; the witness catalog does not replace these host tests.

`cases.json` and `platforms.json` have compiled provenance pins. Each blob is
content addressed. Every fixture has an explicit publication and audit entry,
size and hash qualification, and raw secret scanning even for NUL-filled data.
The 227 old negative controls are retained as inert counterexamples and actual
missing-export controls. Native tests also exercise copied-library mutations,
filesystem-seeded Cargo failures, source omissions, process cleanup, and guard
mutations that must go RED before restoration goes GREEN.

Linux x86-64 execution is qualified by the focused tests. The independently
captured Windows MSVC preview oracle remains distinct, with explicit selection
and byte overlays; fixture selection is not a claim of Windows execution or
portable release certification. Linux-only interpolation witnesses remain
Linux-only. Unsupported preview platforms are refused, not silently admitted.

The retired Python engines and fake semantic libraries are not shipped. Necessary
Python packaging/AST tests and real SDK/Blender integration remain. Whole-repository
Python bytes, including all new compatibility glue and production growth, are
measured against the integration baseline rather than counted by production lines.
