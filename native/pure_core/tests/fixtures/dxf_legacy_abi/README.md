# Independent legacy DXF decode ABI goldens

`analytic.dxf` is handwritten Latin1 input, not writer output. Its six analytic
records are LINE, CIRCLE, ARC, closed POLYLINE, LWPOLYLINE and rational closed
cubic SPLINE. `specification.json` independently states the expected entity
coordinates, metadata and packed type IDs. `analytic.packed` was packed from
that specification, never obtained by calling a decoder or parser. The negative
zero LINE coordinate is asserted byte-for-byte. Layers retain insertion order;
skipped kinds use the legacy ABI's sorted order. True colors carry bit24.
`empty.packed` independently specifies the nonnull empty-input40-byte result.

All fields are little-endian. Header:u32magic44584631,u32version1,i64units;
i64layerCount; name lengths/name bytes/colors; i64entityCount; type IDs, name
lengths/name bytes/colors/closed flags/coordinate counts/f64coordinate run;
i64skippedCount; name lengths/name bytes/counts. Each count/length/value integer
is i64. Missing units use-1 in this unchanged legacy ABI. The Rust tests call
both stable C exports directly; there is no Python reference engine at runtime.
