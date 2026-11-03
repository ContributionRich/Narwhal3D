# Native document observer — live Linux development boundary

The model uses this read-only openNURBS bridge with the persistent Rust application
core. Rust owns membership/order, name/display associations, dirty marks, immutable
snapshot plans and restore differences. Python owns exact host geometry references,
Blender integration and the existing single Undo record. No second history is added.

## Source and runtime admission

Pinned rhino3dm8.35.0 source distribution SHA256:
`8315ec0c177ad4c69e8cc4e6d331c584110e8e8cc9bad294f121f18b4b7ec81a`.

The current Linux CPython3.13 extension matches the official wheel byte-for-byte:
`d5fed4447838a0645bfc82cf6ccc684e1c6ca6d4568533e044adf9754de169d4`.
`narwhal3d/native_document_observer.py` requires the platform's host hash (`_HOSTS`),
its Python ABI, and a qualified observer hash before loading C++. It never builds or
falls back at runtime. The Windows x86_64 host (`cp313-win_amd64`) is recorded in
`_HOSTS` as `1b59225812361ffd89e48eb1caf64a9f505aa1613da2058479b0b766fa976ce7`.

The official pybind11 conduit checks platform ABI/type and lends `BND_ONXModel` only
for the call. No pointer escapes; the GIL stays held. Matched source provenance is
also necessary: conduit alone is not a source-layout certificate. openNURBS symbols
are hidden in this extension. Cross-library class-ID casts are not used; matched C++
RTTI reads model components. The original rhino3dm/Blender binaries are not replaced.

## What is observed

`index(file)` returns `NWIDX01\0`, a u32 little-endian count, and rows consisting of
36 canonical UUID bytes plus a length-prefixed UTF-8 name. It does not serialize
geometry. Stable object-count/display lookup uses the existing native UUID/count
accessors; snapshots, explicit name-index synchronization and persistence perform
stronger observation.

`observe(file)` returns `NWOBS01\0` with the same IDs/names plus length-prefixed
geometry and attribute **comparison witnesses**. They are NOT importable archives,
not document geometry, and must never be exported or reconstructed as a shape.

Each source object is privately duplicated before serialization: user data can ask
to be deleted after writing, which must not modify the live document. Lazy Brep
solidity and bounding-box queries are evaluated on that private copy, so later
read-only picking/classification cannot appear to change document geometry. The actual
native archive stack identifies chunk CRC fields and userdata copy counters while
writing, including nested geometry. Those bookkeeping bytes are normalized in the
private output buffer. All other payload bytes remain. This avoids treating the
copy counters incremented by Undo's native copies as geometric changes; it does not
pattern-match arbitrary geometry bytes or use a lossy geometric checksum.

`raw_observe` is a diagnostic/test interface with a DISTINCT `NWRAW01\0` marker. It
retains valid native archives for round-trip assertions. Rust refuses that marker
as a registry witness. Geometry/attribute archive round trips and canonical witness
stability are tested separately.

Limits:100,000 rows,4096 UTF-8 bytes/name,16MiB/archive,128MiB/batch. Rust validates
complete framing, identity uniqueness, UTF-8, nonempty full witness fields and bounds.
Render-light documents and top-level unknown or uncopyable native userdata currently
refuse explicitly. External OCCT master internals, document settings, layers and
selection are NOT certified by this observer. The counter is not a full-document
revision. Exact geometry/master/recipe handling remains with openNURBS/OCCT.

## Complete attribute snapshots

The binding has no exposed `ObjectAttributes.Duplicate`. Direct cross-library copy
lost user data during qualification. `copy_attributes` therefore asks the original
rhino3dm binding to copy into a private attribute-holder File3dm, preserves that
runtime's copied userdata, and restores complete attribute fields/copy counters.
Its dummy points exist ONLY to own attributes. They never enter the document, are
never used as geometry and are never exported. A snapshot retains its private holder.

## Build and package

```
python3 -B scripts/check_application_core.py --stage-dev
python3 -B scripts/check_document_observer.py --stage-dev --mutations
```

The latter fetches/verifies the pinned source, builds a separate PIC openNURBS copy,
then runs host, loader, Rust-boundary and mutation checks. Supply `--python-include`
or `NARWHAL_PYTHON_INCLUDE` for matching CPython3.13 SDK headers. Compiler caches stay
outside the project; `NARWHAL_OBSERVER_WORK` selects a reusable disk-backed directory.
One compiler job, shared build lock, bounded runtime,1GiB/no-swap compiler scopes
and a4GiB desktop reserve are enforced. Neither Blender nor its wheel is rebuilt.

Windows x86_64 uses the same qualification with the Windows inputs instead of
`--pic-build`. The script calls `vcvars64.bat` itself and builds each variant with MSVC
`/MD`, so openNURBS and zlib must be built `/MD` as well
(`-DCMAKE_MSVC_RUNTIME_LIBRARY=MultiThreadedDLL`):

```
python -B native\document_observer\qualify.py --source-tree <sdist>\rhino3dm-8.35.0 --sdist <sdist.tar.gz> --vcvars "<VS>\VC\Auxiliary\Build\vcvars64.bat" --opennurbs-lib-dir <dir with opennurbsStatic.lib and zlib.lib> --python-lib <CPython 3.13 libs>\python313.lib --python <python.exe> --python-include <CPython 3.13 include> --rust-check <rust check> --core <narwhal_application_core.dll> --output-root <scratch dir> --mutations [--stage-dev]
```

Each variant produces `_narwhal_document_observer.pyd` and receives the same control,
mutation and restored checks as Linux, with identical anchors and RED/GREEN rules. The
loader and registry integration checks follow. Linux requires `--pic-build`; Windows
requires the three Windows options and refuses `--pic-build`. `--stage-dev` installs
into `artifacts/document-observer/windows-x86_64/`.

`windows-x86_64` is a qualified platform once a receipt exists for the pinned host and a
matching library. macOS remains refused until its own pair is qualified.

Development staging keeps prior directories. Normal discovery uses
`artifacts/document-observer/<platform>/` (`linux-x86_64` with `_narwhal_document_observer.so`,
`windows-x86_64` with `_narwhal_document_observer.pyd`); packaged discovery uses
`narwhal3d/native/document-observer/`. Source launch preflight exercises required
registry operations before any graphical runtime is launched.

Release assembly requires a separately supplied, source-bound observer/core pair
and retains existing portability checks. Host development qualification is NOT a
portable release claim. `scripts/stage_document_observer.py` admits `linux-x86_64` and
`windows-x86_64` pairs (library `.so` / `.pyd`, core `libnarwhal_application_core.so` /
`narwhal_application_core.dll`); macOS is still refused. Existing released packages/assets
are unchanged.

## Evidence and limits

Host tests cover direct/raw-call mutations, UUID/name/order, user text, groups,
plot weights, detached complete attributes, sphere/cylinder/circle native archives,
copy-counter stability and native-batch→Rust decoding. Compiled mutations distinguish
omitted geometry/attributes/names, wrong IDs, lost userdata and missing normalization.
Paired loader and registry adapters have independent malformed/lost-reply tests and
assertion mutations. Real model tests exercise missed-write refusal/rebuild and Undo.

A measured5000-sphere workload initially hit the retained-state limit at snapshot17.
Rust now shares unchanged per-object witness records. The same size retained50
snapshots and restored both endpoints successfully. With tracemalloc enabled, its
measured edit-snapshot median/p95 were472.9/485.4ms; process peak RSS435752KiB. This
is an absolute workload measurement, NOT an advertised speedup or whole-frame result.
Full observation remains O(document size) at explicit snapshot boundaries, never
per-frame geometry serialization. Final integrated product/publication evidence is
recorded separately by Main.
