# Narwhal Library core

Rust authority for the user Library: category/asset catalogue, names/IDs, search,
filtering, sorting, grid layout, immutable asset-file publication, and ZIP package
validation/import/export. Geometry is opaque; the application uses its exact
openNURBS/OCCT adapters to create and place `.narwhal` assets.

Build: `python3 scripts/build_library_core.py --stage` from the repository root.
The helper supports `--version` (`narwhal-library-core 1`). Otherwise it reads one
UTF-8 JSON request from stdin, and replies with exactly one JSON object:
`{"ok":true,"result":...}` or `{"ok":false,"error":"..."}`.

Requests specify `op`, absolute `root`, and operation fields. Operations are
`catalog`, `layout`, `create_category`, `rename_category`, `delete_category`,
`add_asset`, `rename_asset`, `move_asset`, `remove_asset`, `export_library`, and
`import_library`. The thin `narwhal3d.native_library.LibraryStore` adapter defines
the host method signatures. There is no Python catalogue or package fallback.

A version-1 `manifest.json` is the only mutable authority. Geometry and preview
files use fresh IDs; manifests publish by atomic rename after validated files are
complete. A bounded operating-system lock serializes writers/readers and releases even
after a hard process crash; the inert lock file is retained to avoid unlink races. Existing immutable blobs are retained
when entries are removed/replaced so open readers and interrupted publications do
not lose data. Automatic garbage collection is intentionally not implemented.

Packages are `.narwhallib` ZIPs containing a versioned manifest, canonical source
files and PNG previews with SHA-256 digests. Imports reject duplicate ZIP members
before the ZIP reader can coalesce names, traversal, symlinks, extra members,
unsupported versions, corrupt checksums and missing references. Merge allocates
fresh IDs and matches category labels case-insensitively; replace validates the
entire input before publishing a new catalogue. Old bytes are not destroyed.

Limits: 64 KiB request; 10,000 assets/categories; 256 MiB per source/member;
32 MiB preview; 1 GiB package/expanded payload; no multi-disk or ZIP64 packages.
Package operations may temporarily hold the bounded package payload in memory.

Tests: `cargo +1.95.0 test --manifest-path native/library_core/Cargo.toml --locked`
and `python3 spike/test_library_store.py`. All package fixtures are synthetic.
Dependency licenses are collected in `licenses/Library-Rust-Dependencies.txt`.
