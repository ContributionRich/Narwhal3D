# Native SVG path and alias grammar ownership

SVG path parsing requires core ABI1 and owned SVG ABI14. Rust owns tokenization,
Unicode numeric admission, absolute/relative commands, line/quadratic-to-cubic
conversion, smooth-control reflection, historical elliptical-arc conversion,
closure and typed parse failures. Python owns bounded ctypes framing, host type
admission, validated output conversion and exception presentation. The public
wrapper shares one adapter instance; no Python parser or geometry replay remains.

## Geometry contract

This does not change manufacturing geometry, kernel operations, recipes or Undo.
SVG elliptical arcs historically import as approximate cubic Beziers, not analytic
ellipses. That existing documented approximation is unchanged, not a new exact
ellipse producer. Lines and elevated quadratics retain their exact cubic control
points. Qualification covers 2,000 generated arcs and 1,000 mixed/reflection paths
with bit-exact control points, plus malformed input/error metadata and ownership
mutations. Existing packed/JSON compatibility exports remain tested. The foreign
SVG import consumer retains its packed native optimization; it never replays the
retired Python parser, and its small-input route calls the required owned parser.

SVG input is bounded to one million code points and combined native output to
64 MiB (coordinates, offsets and flags). Nonfinite geometry is explicitly refused.
The owned grammar requires the bundled Unicode15.1 runtime. The documented input
is a string; nonempty lists of string fragments are not supported. Measured empty
non-string admission exceptions and error types remain covered.

## Alias scope

Rust owns only alias macro grammar: optional prefix, canonical command and
ordered option spans, including prefix backtracking, Unicode word/space behavior,
newline anchor behavior and empty options from all-underscore tokens. The native
adapter requires core ABI1 and alias identity1; malformed replies, unused metadata,
prefix codepoints and out-of-order spans fail explicitly before counters advance.
Inputs above4096 codepoints are refused before binding or allocating buffers.
Falsy host values preserve the prior invalid tuple; truthy nonstrings raise TypeError.

AliasTable state, CRUD, casefold, defaults and status mapping remain Python.
They are separate, not a grammar fallback, and have no native state replacement
in this batch. Tests compare 5,364 grammar cases and preserve all22 defaults.

## Retirement and qualification

Old engines are privately archived under ignored legacy/retired-rust14 with hashes.
Independent references under spike are test-only and never loaded by application
code. Missing/stale libraries or malformed native replies fail explicitly, never
silently replaying Python. ABI package probes exercise actual geometry, classified
errors and prefix/options rather than checking symbol names alone. Matching native
artifacts must be qualified and staged before activation and isolated GUI runs.

Warm SVG measurements on this Linux host were about16us for a24-byte path versus
7.6us for the Python oracle, and9.34ms for40,004bytes versus11.64ms. Consolidating
ownership is not a claim that every tiny FFI call is faster. No new release or
version change is included; Windows source checks are not runtime certification.
