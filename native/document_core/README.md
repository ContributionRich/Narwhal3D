# Narwhal document core

`narwhal-document-core` is a dependency-free Rust executable for creating, validating,
examining, and extracting the built-in `.narwhal` document container. The packaged
executable has no external language runtime requirement. Version 1 stores members
without compression.

The crate is licensed under `GPL-3.0-or-later`.

## Command-line protocol

```text
narwhal-document-core --version
narwhal-document-core pack <staging-dir> <target-file>
narwhal-document-core unpack <source-file> <output-dir>
narwhal-document-core inspect <source-file>
```

`--version` writes exactly:

```text
narwhal-document-core 1
```

`pack` recursively includes regular files and rejects symbolic links, sockets, devices,
and every other special file. Members are opened without following links and those open
handles are retained for hashing and payload copying; member paths are never reopened for
content. Linux directory traversal uses pinned directory handles. File and directory
identities are checked before and after copying, so replacement, symlink-swap, and source
mutation races fail closed. The resulting table and payloads are ordered by canonical
member path, so identical staging content produces identical bytes. The destination is
written to a uniquely named sibling temporary file, synchronized, and atomically renamed
over the destination. A failed pack removes its temporary file and leaves an existing
destination intact.

`unpack` requires that the output path **does not exist** and that its parent is an
existing real directory. This is intentionally different from accepting an existing empty
directory: a complete tree cannot be atomically published into such a directory on every
supported platform. The command validates the entire container, extracts and synchronizes
all members in a unique sibling temporary directory, then performs one no-replace atomic
directory rename to the requested output path. It never publishes individual members. If
validation, extraction, or publication fails, the temporary tree is removed and the tool
does not create the requested output path. It never interprets a member path as an absolute
or parent-relative filesystem path.

`inspect` performs the same complete validation as `unpack`, then writes one line per
member in table order:

```text
<lowercase-sha256>\t<decimal-size>\t<canonical-path>
```

Invalid input and I/O failures produce a diagnostic on standard error and exit status 1.
Invalid command syntax produces exit status 2.

## Version 1 binary layout

All integers are unsigned and little-endian. There is no alignment padding.

### Fixed 32-byte header

| Offset | Size | Field |
|---:|---:|---|
| 0 | 8 | Magic bytes `4e 41 52 57 48 41 4c 00` (`NARWHAL\0`) |
| 8 | 4 | Format version, exactly `1` |
| 12 | 4 | Entry count |
| 16 | 8 | Total byte size of the entry table |
| 24 | 8 | Total byte size of the payload area |

The entry table begins at byte 32. It contains exactly `entry_count` records with this
variable-length layout:

| Size | Field |
|---:|---|
| 4 | UTF-8 path byte length |
| path length | Canonical UTF-8 member path |
| 8 | Absolute byte offset of the member payload from the start of the container |
| 8 | Member payload size |
| 32 | SHA-256 of the exact member payload bytes |

The payload area begins at `32 + table_size`. Payloads are concatenated without gaps in
the same sorted order as their records. The first payload offset must equal the payload
area start; every later offset must equal the end of the preceding payload; the final end
must equal the container length. Extra table bytes, payload gaps, overlaps, reordered
ranges, and trailing bytes are invalid.

## Canonical member paths and required members

A canonical member path:

- is valid UTF-8 and between 1 and 1024 bytes;
- is relative and uses `/` separators only;
- has no empty, `.` or `..` component;
- has no colon (including Windows alternate-data-stream syntax), backslash, or NUL;
- is not Unix-absolute or Windows drive-qualified; and
- appears in strictly increasing byte order with no duplicate.

The encoded path-length field and the canonical path are both bounded to 1024 bytes before
allocation or filesystem use.

Every container has exactly one root `manifest.json` and exactly one root `document.3dm`.
Exact master BREP members are allowed only as
`masters/<lowercase-uuid>.brep`, where the UUID has the `8-4-4-4-12` lowercase hexadecimal
shape. Nothing else may be stored below `masters/`, and a `.brep` member may not appear
elsewhere.

## Resource and validation limits

Version 1 enforces these inclusive ceilings:

- 10,000 members;
- 1024-byte encoded and canonical path;
- 512 MiB per member;
- 4 GiB total payload and 4 GiB total container.

Lengths, offsets, table sizes, payload sums, range ends, allocation sizes, and platform
`usize` conversions are checked before use. SHA-256 is implemented within this crate and
covered by published standard vectors, including empty input, `abc`, a multi-block vector,
and one million `a` bytes.

## Build and test

The toolchain is pinned by `rust-toolchain.toml`:

```text
cargo build --release --locked
cargo test --all-targets --locked
cargo clippy --all-targets --locked -- -D warnings
```

The Cargo manifest has no dependencies. Linux builds omit the linker build ID and Windows
GNU builds disable PE timestamp insertion for deterministic packaged artifacts. Build
output remains under the ignored local `target/` directory; this source tree does not
contain packaged binaries.
