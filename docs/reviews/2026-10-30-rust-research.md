# Reviewed Rust and command-behavior work

This is development-source progress for the next release. It does not change the
already-published Linux/Windows0.4.8 artifacts or automatically enter the separate
Mac0.4.8 candidate.

## Integrated

- Nearest ACI colour calculation now has an optional in-process Rust path. Python
  retains validation, exception ordering and the cheap nine-colour exact lookup.
  The1–9 palette and explicit10–255 limitation are unchanged.
- Print-width files of at least256 characters can use the reviewed Rust parser.
  Python retains file opening, strict decoding, universal newlines and a complete
  fallback. Tiny files avoid the fixed boundary cost; incompatible Unicode table
  versions, unsupported strings and failed native responses fall back explicitly.
- Text wrapping now refuses an exhausted paragraph that cannot make progress,
  rather than appending spans indefinitely. This intentional safety fix preserves
  ordinary kerning, zero width, infinite width and empty-paragraph behavior.
- UI source bundles include reviewed native files, not unregistered experiments
  merely present in the same source directory.

The focused in-process measurements include Python conversion/dispatch and file
I/O: nearest colour queries improved from about9.7µs to3.3–3.5µs, and the tested
7.5KB/150KB width files improved by roughly1.3–1.4×. These are measured workloads,
not whole-application speedup claims. Exact-colour lookup stays in Python because
it is cheaper than crossing the FFI boundary.

All sixteen isolated port drivers passed407 tests in review. Only the two bounded
accelerators above are integrated. Mutable-state ports still need Python attribute,
alias, large-integer, Unicode, error-order and real boundary-cost validation.
Their IPC prototypes are not installed; a fast Rust-only benchmark is insufficient.

## Command research: use evidence, not blanket rewrites

Recent native-UI observations corroborate the implemented Polygon count memory and
radius/bearing behavior, basic Rectangle/Ellipse construction and several line
Trim/Split/Offset/Extend and Move paths. Matching behavior needs regression coverage,
not another implementation.

Remaining concrete gaps include:

- Divide currently places points on a64-chord approximation. A radius10 NURBS circle
  divided into3 segments produced interior points about0.01285546 units off the
  curve. An exact curve-evaluation/arc-length route is needed before layering more
  options onto that sampled implementation.
- Rotate Copy mode remains active in the measured reference workflow and preserves
  the copy on Escape. Further-copy reference and Undo grouping need decisive coverage
  before changing the command's interaction/transaction model.
- Center-mode Arc direction can differ for the same typed endpoints when the cursor
  changes sides. Mode, construction-plane and cancellation coverage remain necessary.

These gaps are documented, not claimed implemented. Cursor screenshots are not
numerical geometry measurements, displayed precision is not unlimited accuracy, and
observed session defaults are not universal factory defaults. Raw research captures,
private settings and unreviewed prototypes are not part of public release packages.
