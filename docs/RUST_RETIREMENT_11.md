# Native-only parsing and display formatting

Current source consolidates three complete computation paths in the dependency-free
Rust pure core. This is not a Python-free application: Blender/openNURBS/OCCT host
integration and the existing unified Undo still retain their established adapters.
No document schema, geometry representation, or Undo owner changed.

## Retired runtime paths

- **Print-width parsing:** all sizes use `narwhal_pure_core_printwidths`. The old
  short-file Python parser/bypass is gone. Python only reads strict UTF-8 files,
  frames the ABI and maps explicit failures. Native input limit: 1 MiB.
- **Length display:** Rust normalizes mode names, validates ranges, rounds and
  renders Decimal, Fractional and Feet & Inches. No Python Decimal arithmetic,
  greatest-common-divisor loop or string-building formatter remains.
- **Macro tokenization:** every supported input uses the checked native JSON ABI.
  The Python quote/comment splitter, classifier and length-threshold bypass are
  gone. Host JSON/packed output converters are retained as ABI glue, not parsers.
  Missing/stale libraries, malformed output, non-UTF-8 text and over-1-MiB macros
  are explicit errors, never successful-looking Python replay.

Previous production implementations are backed up privately under the ignored
`legacy/retired-rust11/`, with SHA-256 receipts. No application imports the archive.
Independent frozen test oracles under `spike/` are development-only and are not
included in the application runtime payload.

## Correctness discoveries

Native-only conversion exposed gaps previously hidden by Python replay:

- Python letter categories exclude Roman letter-numerals/combining marks that
  Rust's Unicode Alphabetic property includes. Macro classification now uses a
  measured CPython 3.13 Unicode 15.1 letter table.
- U+001C–U+001F split tokens under Python's whitespace contract.
- Empty option names remain admitted by the existing tokenizer contract.
- A leading `!` on a non-command value such as `!123` remains text, not Cancel.
- Fractional display first rounds decimal multiplication to context significant
  digits (half-even), then quantizes half-up. Omitting that first step changes
  `0.0936` at denominator 16 and context precision 2 from `1/8` to `1/16`.
- Large decimal coefficients use digit vectors and small-divisor long division;
  they never wrap or panic through a fixed-width integer inside the C ABI.

## Bounds and supported decimal context

Length display supports the normal half-even decimal context, including varying
positive context precision. Non-default rounding, exponent limits, clamp or trap
configuration is explicitly refused, not silently approximated. The application
uses the standard context. Native output is bounded to 4096 bytes; mode text to
1 MiB. Native-owned errors retain the existing host exception classes/messages
for supported inputs. These bounds/refusals are deliberate safety constraints.

## Qualification and packaging

`scripts/check_pure_core.py` requires the display, print-width and macro exports
in addition to the existing parser ABI. Its real semantic probes run before
staging and are reused by Linux and Windows package validation. Wrong/missing
exports or mismatched witness values refuse packaging; Python cannot hide them.
The source gate includes the new owned suites and independent differential tests.
Mutation tests require RED before restoring GREEN; isolated Rust mutations never
modify the live native source or staged library.

Measured Linux microbenchmark: a repeated 259-byte macro is about 1.4× faster in
Rust, while a repeated 5-byte macro is about 3.6× slower (roughly 8 versus 2 µs).
This is an explicit consolidation tradeoff, not a universal speedup claim. Windows
cross-compilation is not Windows runtime certification. No release binaries or
version numbers change in this source round.
