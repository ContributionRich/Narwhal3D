#!/bin/sh
# Build Narwhal's source-only ONBREP 1 helper against one exact official
# openNURBS source archive. This is an explicit developer command: the app never
# downloads or compiles it at runtime, and release assembly does not include it.
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
COMMIT=eb92af3ba1806b0a34a99aba0d3bda83e3d46083
ARCHIVE_SHA256=1a8bb8dbe363f4969f679f11810251433416b62e595a6f02c447381a9f2128fa
ARCHIVE_URL=https://github.com/mcneel/opennurbs/archive/$COMMIT.tar.gz
IMAGE=quay.io/pypa/manylinux_2_28_x86_64@sha256:1e7b8460ad8fb3fb0fbe1cd818845159ac896b4fd17452d56e892c291f3b97a8
SOURCE="$ROOT/native/brep_writer/main.cpp"
VERIFIER="$ROOT/scripts/verify_brep_writer.py"
CACHE_DIR="$ROOT/artifacts/brep-writer-source"
ARCHIVE="$CACHE_DIR/opennurbs-$COMMIT.tar.gz"
OUTDIR="$ROOT/artifacts/brep-writer/linux-x86_64"
OUTPUT="$OUTDIR/brep_writer-linux-x86_64"
MODE=container

case "${1:-}" in
    --self-check)
        test -f "$SOURCE" && test ! -L "$SOURCE"
        test -f "$VERIFIER" && test ! -L "$VERIFIER"
        grep -q 'brep_writer ONBREP 1' "$SOURCE"
        grep -q "$COMMIT" "$0"
        grep -q "$ARCHIVE_SHA256" "$0"
        grep -q 'manylinux_2_28_x86_64@sha256:' "$0"
        grep -q -- '--network none' "$0"
        grep -q -- '--read-only' "$0"
        echo "build_brep_writer_source.sh self-check: OK ($COMMIT)"
        exit 0
        ;;
    --host) MODE=host ;;
    "") ;;
    *) echo "usage: $0 [--host|--self-check]" >&2; exit 2 ;;
esac

for path in "$CACHE_DIR" "$OUTDIR"; do
    if test -L "$path"; then
        echo "refusing symlinked build path: $path" >&2
        exit 1
    fi
done
mkdir -p "$CACHE_DIR" "$OUTDIR"
if test ! -f "$ARCHIVE"; then
    TEMP="$ARCHIVE.downloading"
    rm -f "$TEMP"
    curl --proto '=https' --tlsv1.2 --fail --location --output "$TEMP" "$ARCHIVE_URL"
    printf '%s  %s\n' "$ARCHIVE_SHA256" "$TEMP" | sha256sum --check --status
    mv "$TEMP" "$ARCHIVE"
fi
printf '%s  %s\n' "$ARCHIVE_SHA256" "$ARCHIVE" | sha256sum --check --status || {
    echo "openNURBS source archive hash mismatch" >&2
    exit 1
}

if test "$MODE" = container; then
    ENGINE=
    for candidate in podman docker; do
        if command -v "$candidate" >/dev/null 2>&1; then
            ENGINE=$candidate
            break
        fi
    done
    if test -z "$ENGINE"; then
        echo "podman or docker is required; use --host only for a local non-portable build" >&2
        exit 1
    fi
    "$ENGINE" run --rm --network none --read-only --cap-drop=all \
        --security-opt no-new-privileges --security-opt label=disable \
        --tmpfs /tmp:rw,size=128m --tmpfs /build:rw,size=3g \
        -v "$SOURCE":/input/main.cpp:ro \
        -v "$ARCHIVE":/input/opennurbs.tar.gz:ro \
        -v "$OUTDIR":/out "$IMAGE" bash -c '
            set -eu
            mkdir -p /build/opennurbs
            tar -xzf /input/opennurbs.tar.gz -C /build/opennurbs --strip-components=1
            make -C /build/opennurbs -j2 libopennurbs_public.a \
                ON_GNU_OPTIMIZER_FLAGS=-O2 >/tmp/opennurbs-build.log 2>&1 || {
                    tail -40 /tmp/opennurbs-build.log
                    exit 1
                }
            g++ -O2 -std=c++14 -I/build/opennurbs /input/main.cpp \
                /build/opennurbs/libopennurbs_public.a \
                -static-libstdc++ -static-libgcc -lpthread \
                -Wl,--build-id=none -o /out/brep_writer-linux-x86_64
            strip /out/brep_writer-linux-x86_64
        '
else
    BUILD=$(mktemp -d)
    trap 'rm -rf "$BUILD"' EXIT INT TERM
    tar -xzf "$ARCHIVE" -C "$BUILD" --strip-components=1
    make -C "$BUILD" -j2 libopennurbs_public.a ON_GNU_OPTIMIZER_FLAGS=-O2
    if ! g++ -O2 -std=c++14 -I"$BUILD" "$SOURCE" "$BUILD/libopennurbs_public.a" \
            -static-libstdc++ -static-libgcc -lpthread -Wl,--build-id=none -o "$OUTPUT"; then
        echo "static libstdc++ unavailable; retrying a host-local non-portable link" >&2
        g++ -O2 -std=c++14 -I"$BUILD" "$SOURCE" "$BUILD/libopennurbs_public.a" \
            -static-libgcc -lpthread -Wl,--build-id=none -o "$OUTPUT"
    fi
    strip "$OUTPUT" 2>/dev/null || true
fi

VERIFY_PYTHON=${NARWHAL_BREP_VERIFY_PYTHON:-}
if test -z "$VERIFY_PYTHON"; then
    for candidate in \
        "$ROOT/tools/blender-patched/5.2/python/bin/python3.13" \
        "$ROOT/tools/blender-5.2.2-linux-x64/5.2/python/bin/python3.13" \
        python3; do
        if "$candidate" -c 'import rhino3dm' >/dev/null 2>&1; then
            VERIFY_PYTHON=$candidate
            break
        fi
    done
fi
if test -z "$VERIFY_PYTHON"; then
    echo "a Python interpreter with rhino3dm is required for archive verification" >&2
    exit 1
fi
"$VERIFY_PYTHON" "$VERIFIER" "$OUTPUT"
if test "$MODE" = host; then
    cp "$BUILD/LICENSE" "$OUTDIR/openNURBS-LICENSE.txt"
else
    tar -xOf "$ARCHIVE" --wildcards '*/LICENSE' > "$OUTDIR/openNURBS-LICENSE.txt"
fi

if test "$MODE" = container; then
    for tool in objdump readelf sha256sum; do
        command -v "$tool" >/dev/null 2>&1 || {
            echo "$tool is required for release-baseline verification" >&2
            exit 1
        }
    done
    MAX_GLIBC=$(objdump -T "$OUTPUT" | grep -o 'GLIBC_[0-9.]*' | sort -V | tail -1)
    test -n "$MAX_GLIBC" || { echo "no GLIBC symbol version was measurable" >&2; exit 1; }
    HIGHEST=$(printf '%s\n' GLIBC_2.28 "$MAX_GLIBC" | sort -V | tail -1)
    if test "$HIGHEST" != GLIBC_2.28; then
        echo "helper requires $MAX_GLIBC, above the GLIBC_2.28 release ceiling" >&2
        exit 1
    fi
    if readelf -d "$OUTPUT" | grep -Eq 'libstdc\+\+|libgcc_s'; then
        echo "container build unexpectedly depends on host C++ runtime libraries" >&2
        exit 1
    fi
    echo "maximum GLIBC: $MAX_GLIBC"
else
    echo "WARNING: --host output is for local testing and is not release-portable" >&2
fi

sha256sum "$OUTPUT"
echo "linked libraries:"
ldd "$OUTPUT" 2>/dev/null || true
echo "source-only helper built at $OUTPUT"
