# Reader24 independent original outcomes

Captured from the original `95d92d17` Python **reader**, with its optional native
route disabled, before retirement. The original reader source SHA-256 and
source commit are in `capture.json`. No reference parser or private archive is
required at test time. Writer23 bytes provide some inputs only; their expected
reader answers were separately obtained from the original reader.

`corpus.bin` contains 606 named inputs and typed answers (84 success, 522 error).
`manifest.tsv` and the `input`/`expected` names in JSON identify archive members,
not loose files. Error JSON preserves exact classes, messages and arguments;
success JSON preserves dict insertion order, types and every f64 bit, including
signed zero, subnormals and nonfinite file data. The Rust test compares the exact
typed result bytes and diagnostic codes. Host tests compare the original public
results and errors. They do not treat nonfinite file data as safe kernel geometry.

Archive format (little-endian): `DXFCAP24`, u32 count; for each member: u32 name
length, ASCII name, u32 interpreter decimal limit, u64 input length, input bytes,
u64 answer length, typed result bytes (empty for errors), i64 diagnostic code.
The typed protocol is documented in `../../dxf_reader24.h`.

Coverage includes analytic LINE/CIRCLE/ARC/POLYLINE/LWPOLYLINE/SPLINE; grouped
spline axes, rational weights, flags/counts/error ordering; Latin1 metadata;
all 256 byte prefixes for both numeric grammars; underscores; universal newlines;
true color/ACI separation and duplicate layers; absent/negative/large units;
ordered skipped and bulge records; missing fields; duplicate invalid fields;
binary/truncated/version refusals; CPython decimal limits 640/4300/disabled;
and 1,000 independent deterministic random LINE records (6,000 f64 inputs).
