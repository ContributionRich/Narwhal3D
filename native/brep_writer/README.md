# Exact ONBREP 1 BRep writer

This directory contains a small, source-only helper that constructs exact openNURBS
`ON_NurbsCurve`, `ON_NurbsSurface`, and `ON_Brep` objects from the whitespace-delimited
`ONBREP 1` protocol. Curved geometry remains NURBS geometry; this helper does not replace
it with a polygon mesh.

## License and dependency boundary

This is **Narwhal-authored source licensed under GPL-3.0-or-later**. The directory does
not include openNURBS source, headers, libraries, binaries, or other third-party assets.
Building it requires separately obtaining the official openNURBS headers and library and
complying with the terms that apply to those materials.

Publishing this source does **not** determine or resolve the terms for redistribution of
a binary linked with openNURBS. Anyone building or distributing such a binary must assess
and satisfy the applicable openNURBS and GPL requirements separately.

`scripts/build_brep_writer_source.sh` is the pinned Linux developer build recipe. It
fetches exact official openNURBS commit `eb92af3ba1806b0a34a99aba0d3bda83e3d46083`
and writes its local result under `artifacts/`; it does not add the binary to a release.

## Interface

The protocol identity is exactly:

```text
brep_writer ONBREP 1
```

Supported invocations:

- `brep_writer --version` writes
  `brep_writer ONBREP 1 opennurbs <version>` to stdout.
- `brep_writer box` writes an archive containing the openNURBS unit-box BRep to stdout.
- `brep_writer` reads one `ONBREP 1` description from stdin and writes its BRep archive
  to stdout.

Successful `box` and stdin conversions write only openNURBS archive bytes to stdout and
write `opennurbs <version>` to stderr. The archive uses openNURBS 3dm archive version 60.
Errors and the final `ON_Brep::IsValid` refusal are written to stderr; failure exits with
status 2.

## ONBREP 1 description

All fields are whitespace-delimited:

```text
ONBREP 1
tol <finite non-negative tolerance>
vertices <count>
  <x> <y> <z> ...
edges <count>
  <start-vertex> <end-vertex> <curve3d> ...
faces <count>
  <reversed> <surface> <loop-count>
    <loop-type> <trim-count>
      <edge-index> <reversed3d> <singular-vertex-index> <curve2d> ...
```

A curve is:

```text
<dimension> <rational> <order> <cv-count>
<cv values in homogeneous form when rational>
<order + cv-count - 2 knots>
```

An edge curve must have dimension 3 and a trim curve dimension 2. A surface is:

```text
<rational> <order-u> <order-v> <cv-u> <cv-v>
<cv-u * cv-v control points, U outer and V inner>
<order-u + cv-u - 2 U knots>
<order-v + cv-v - 2 V knots>
```

Flags are exactly `0` or `1`. Loop type `1` is outer and `2` is inner. A trim edge index
of `-1` denotes a singular trim: its vertex index must be valid and `reversed3d` must be
`0`. A non-singular trim must reference a valid edge and use singular vertex index `-1`.
Trailing tokens are rejected.

## Parser limits

The parser checks arithmetic before allocation or indexing and applies these conservative
limits:

| Item | Limit |
|---|---:|
| Vertices | 100,000 |
| Edges | 100,000 |
| Faces | 50,000 |
| Loops per face | 4,096 |
| Loops in one description | 100,000 |
| Trims per loop | 100,000 |
| Trims in one description | 100,000 |
| Curve or surface order | 64 |
| Curve control points | 262,144 |
| Curve knots | 262,206 |
| Surface control points per direction | 4,096 |
| Surface control points in one grid | 262,144 |
| Surface knots per direction | 4,158 |
| Vertex/CV/knot numeric values in one description | 8,000,000 |
| In-memory output archive | 512 MiB |
| Characters in one protocol token | 128 |

Control-point counts must be at least their corresponding order. Every tolerance,
coordinate, control-point component, and knot must be finite. Section keys, all topology
indices, singular-trim fields, dimensions, flags, counts, and complete input availability
are validated before the corresponding openNURBS array is accessed. The completed BRep is
still refused unless `ON_Brep::IsValid` succeeds.
