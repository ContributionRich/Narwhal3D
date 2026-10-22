# Narwhal geometry-core

`narwhal-geometry-core` is a dependency-free Rust `cdylib`/`staticlib` whose public,
versioned C ABI is the ownership and validation boundary for a private C++17 OCCT
adapter. It constructs, inspects, and topology-locally edits exact OCCT BReps. It
never creates viewport meshes or substitutes approximate N-sided filling.

## ABI 4

All storage crossing the public ABI is caller-owned. No Rust or C++ allocation is
returned to the caller. Byte lengths and capacities are bounded to 512 MiB.
Negative return values are stable failures:

| value | meaning |
|---:|---|
| `-1` | invalid argument, pointer, capacity, or dimensions |
| `-2` | 512 MiB limit exceeded |
| `-3` | reserved buffer-too-small error |
| `-4` | OCCT construction failure |
| `-5` | malformed/unreadable BinTools BRep |
| `-6` | unexpected adapter failure |
| `-7` | exact local-move policy refusal |
| `-8` | boundary-exact candidates exceed the strict smoothness gate |
| `-9` | invalid, stale, released, or random edit-session handle |

```c
uint32_t narwhal_geometry_core_abi_version(void);
uint64_t narwhal_geometry_core_capabilities(void);

int64_t narwhal_geometry_core_make_box_brep(
    double dx, double dy, double dz, uint8_t *out, size_t capacity);

int32_t narwhal_geometry_core_inspect_brep(
    const uint8_t *input, size_t input_length,
    struct NarwhalBrepInspectionV1 *out);

int64_t narwhal_geometry_core_move_component_brep(
    const uint8_t *input, size_t input_length,
    uint32_t component_kind, uint64_t one_based_index,
    double dx, double dy, double dz,
    uint8_t *out, size_t capacity,
    struct NarwhalLocalMoveCertificateV3 *certificate);

int32_t narwhal_geometry_core_edit_session_import(
    const uint8_t *input, size_t input_length, uint64_t *output_handle);
int64_t narwhal_geometry_core_edit_session_move_mode(
    uint64_t handle, uint32_t validation_mode,
    uint32_t component_kind, uint64_t component_index,
    double dx, double dy, double dz, uint8_t *out, size_t capacity,
    struct NarwhalLocalMoveCertificateV3 *certificate);
int64_t narwhal_geometry_core_edit_session_move(
    uint64_t handle, uint32_t component_kind, uint64_t component_index,
    double dx, double dy, double dz, uint8_t *out, size_t capacity,
    struct NarwhalLocalMoveCertificateV3 *certificate);
int32_t narwhal_geometry_core_edit_session_release(uint64_t handle);
```

`make_box_brep` uses a two-pass contract. Call it with `out == NULL` and
`capacity == 0` to obtain the required byte count, allocate that many bytes, then
call again. If a nonzero capacity is too small, it returns the required count and
leaves the buffer untouched. Dimensions must be finite and strictly positive.

`NarwhalBrepInspectionV1` is a 136-byte `repr(C)` record containing its byte size,
ABI version, validity and airtight flags, unique face/edge/vertex counts, exact
OCCT volume, and counts for plane, cylinder, cone, sphere, torus, Bezier,
B-spline, revolution, extrusion, offset, and other faces. Inspection zeros the
record before parsing and again after an adapter error. Its binary layout remains
ABI-1; ABI 4 adds rather than changes exports.

`move_component_brep` uses the same caller-owned two-pass convention. Component
kinds are `1=face`, `2=edge`, and `3=vertex`; indices are OCCT stable-map, one-based
indices. Rust validates all spans, limits, kinds, indices, finite nonzero translations,
and owns the policy constants: 25 candidate combinations, 33 boundary samples,
`1e-14` zero-vector threshold, and `2e-6` radian internal-normal-jump limit.

The 248-byte `NarwhalLocalMoveCertificateV3` records status/refusal reason,
validity, airtightness, self-intersection, topology-count preservation, uniquely
mapped moved vertices, mapped edge/face incidence, unchanged edge/face geometry,
selected-match count and translation error, comparison/build/sewing tolerances,
maximum boundary error and applicable internal-knot normal jump, topology counts,
volume, and every face-type count.

`quality_scope_bits` says which checks apply and `quality_checked_bits` says which
were completed. In particular, a zero normal jump is meaningful only when
`QUALITY_INTERNAL_KNOT` is set. Counts separately report all reconstructed faces,
reconstructed B-spline faces whose complete boundary correspondence was measured in
both directions, and reconstructed faces with applicable full-multiplicity internal
knots. The certificate does not claim abstract topology equivalence: it names the
narrowly verified count, vertex-map, incidence, unchanged-geometry, and selected-part
properties independently. Refused candidates may report the internal
`verification_failure_stage/detail` that prevented acceptance; successful certificates
set both fields to zero.

The private implementation translates only the selected final component, rebuilds
incident exact line/B-spline edges at moved endpoints, reconstructs affected faces
as exact planar faces or boundary-interpolating 3/4-curve Coons surfaces, and handles
five-edge faces only by joining one adjacent curve pair before the same natural Coons
construction. Every candidate is sewn into one solid and must preserve validity,
airtightness, topology counts, a unique moved/unmoved vertex map, mapped edge/face
incidence, unchanged edge/face geometry, exact selected-part translation, low-degree
reconstructed NURBS representation, and self-intersection validity. A
boundary-exact five-edge candidate above the angular gate refuses explicitly.
Untouched faces are not restricted to a whitelist; exact cone, torus, Bezier,
revolution, extrusion, offset, and other OCCT families are accepted when their mapped
geometry and incidence survive unchanged. Hidden regressions currently construct and
move fused solids with untouched cone and torus faces.

Every decoded BRep must consume the input exactly—trailing bytes are malformed—and
is capped before expensive validation at 100,000 faces, 500,000 edges, and 500,000
vertices. Input/output/certificate overlap is rejected. Edge identity uses an exact
`GeomConvert_CurveToAnaCurve` semantic classifier for LINE, CIRCLE, ELLIPSE, and
BSPLINE, followed by bidirectional geometric checks.

The private `narwhal_occt_adapter_*` symbols catch `Standard_Failure`,
`std::exception`, and all other C++ exceptions. BReps are encoded with OCCT
`BinTools` format version `CURRENT`, with both triangulation and triangulation
normals disabled. Exact mass properties also explicitly disable triangulation.

The canonical C/C++ declaration is
`include/narwhal_geometry_core.h`. It contains all public constants, enums, structures,
prototypes, C++ guards, and C/C++ static layout assertions. Every build compiles,
links, and executes C and C++ consumers against both static and shared smoke providers.

## Edit sessions and current performance

ABI 4 adds Rust-owned edit sessions. Import parses one exact BinTools BRep once and
caches its immutable OCCT shape, stable topology maps, edge→vertex and face→edge
incidence, semantic component types, measures/centroids, vertex positions, and derived
tolerances. The public handle is a generation-checked `uint64_t`; no C++ pointer crosses
the public ABI. Rust stores private adapter pointers in a mutex-protected registry,
serializes access per session, rejects stale/double-release/random handles, and releases
the C++ state through Rust ownership. Registry/session poison and unwinding fail closed.

No move chains from a prior result. COMMIT starts from a topology-isolated copy of the
immutable commit template. LIVE uses the cached original topology read-only and copies
unchanged faces/boundary edges before construction. A direction plan keyed by component,
active-axis mask, and normalized direction caches selected topology closure, moved
vertices, changed-edge reconstruction set, affected-face strategies, and the uniquely
certified five-edge merge choice. Every changed distance still rebuilds geometry and
reruns validity, airtightness, self-intersection, topology-count, boundary/smoothness,
and selected-translation checks.

`NARWHAL_GEOMETRY_VALIDATION_LIVE` may defer only mapped-incidence and unchanged-geometry
certification; their V3 scope bits and verdict fields remain clear. The legacy session
move export always routes through `NARWHAL_GEOMETRY_VALIDATION_COMMIT`, which performs
the full V3 checks. The last exact mode/kind/index/vector result remains memoized, but
identical-request timing is reported separately and is not the distinct-drag gate.

Release distinct-drag measurements use one 0.45 warm-up followed by the five distinct
+X distances 0.70, 0.65, 0.60, 0.55, and 0.50 with preallocated output:

| component | LIVE median / mean | COMMIT median / mean | Python baseline |
|---|---:|---:|---:|
| face2 | 599.14 / 541.90 ms | 247.94 / 301.96 ms | 1,381 ms |
| edge20 | 238.71 / 240.33 ms | 332.69 / 364.45 ms | 1,128 ms |
| vertex1 | 139.89 / 189.64 ms | 464.75 / 469.67 ms | 195 ms |

Face and edge clear the 10% LIVE target by both median and mean. Vertex clears it by
median (28.3%) but not mean (2.7%); the exact 0.70 reconstruction is the measured
outlier. COMMIT remains faster than Python for face/edge but not vertex. Exact-repeat
memoized calls are about 0.0004 ms. The stateless ABI remains the fallback/parity path.

## Reproducible OCCT source identity

The crate is pinned to official OCCT tag `V8_0_1`, whose annotated tag peels to
commit `b8f597c677811d1f9f4d8a97f5ae2825c0353a42`. `NARWHAL_OCCT_SOURCE_DIR` is
mandatory and must name that official source root. A pre-flattened include directory
is deliberately not accepted as the primary input.

Without invoking Git, `build.rs` verifies:

- `adm/cmake/version.cmake` declares exactly 8.0.1;
- SHA-256 identities of the version file, `Standard_Version.hxx.in`,
  `BinTools.hxx`, and the OCCT license exception match the pinned source;
- when checkout metadata is present, `.git/HEAD` or its direct ref resolves to the
  pinned peeled commit.

It recursively discovers every `.hxx`, `.lxx`, and `.gxx` beneath `<source>/src`,
rejects basename collisions when contents differ, and creates a deterministic flat
include tree in Cargo's `OUT_DIR`. On Unix it uses symlinks with a copy fallback;
other platforms copy. Because official source stores `Standard_Version.hxx` as a
CMake template, the builder generates that header in the flat tree and verifies its
macros say exactly 8.0.1.

## Build and test

`NARWHAL_OCCT_LIB_DIR` is also mandatory. `build.rs` invokes system `c++` and `ar`
directly, archives the private adapter, and links exact absolute OCCT shared-library
paths. Hashed wheel filenames are resolved by stable toolkit prefixes such as
`libTKBin-`; missing or ambiguous matches abort the build.

The produced shared library contains the relocatable runtime path `$ORIGIN/occt`.
Integration must place required OCCT shared libraries in an `occt` directory beside
`libnarwhal_geometry_core.so`. Development tests instead set `LD_LIBRARY_PATH` to
the supplied no-VTK library directory.

Run these exact commands from the repository root:

```sh
export NARWHAL_OCCT_SOURCE_DIR="$PWD/vendor/occt-src"
export NARWHAL_OCCT_LIB_DIR="$PWD/tools/blender-patched/5.2/python/lib/python3.13/site-packages/cadquery_ocp_novtk.libs"
export LD_LIBRARY_PATH="$NARWHAL_OCCT_LIB_DIR"

cargo fmt --check --manifest-path native/geometry_core/Cargo.toml
cargo clippy --manifest-path native/geometry_core/Cargo.toml --all-targets -- -D warnings
cargo test --manifest-path native/geometry_core/Cargo.toml
cargo test --release --manifest-path native/geometry_core/Cargo.toml \
  benchmark_local_move_direct_abi -- --ignored --nocapture
cargo build --release --manifest-path native/geometry_core/Cargo.toml
```

Linux release outputs are
`native/geometry_core/target/release/libnarwhal_geometry_core.so` and
`native/geometry_core/target/release/libnarwhal_geometry_core.a`.
