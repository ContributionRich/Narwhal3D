# PDF reference Pictures (Linux)

This helper rasterizes PDF page **1 only** through source-pinned sibling Poppler
`pdfinfo` and `pdftoppm` in the Linux application payload; it is not PDF vector/geometry import. Picture announces page1/total and
how many pages were omitted. PNG/JPEG behavior is unchanged. PDF JavaScript, URL
navigation and embedded actions are not executed by these raster/metadata tools.

The host must launch it with the existing native converter supervisor:20 seconds,
512MiB, bounded captured output, no shell. The helper additionally applies Linux
12-second CPU,512MiB address-space,64MiB output-file,64-descriptor limits and
no-new-privileges/seccomp socket denial inherited by Poppler. This is constrained
rasterization, not a general-purpose hostile-code sandbox. Other platforms refuse
by name until their renderer/containment is qualified.

The visible PDF CropBox is rasterized, matching the dimensions reported by pdfinfo.

Policy: <=64MiB local regular-file input, <=10,000 pages, unencrypted PDF only,
first-page dimensions <=200in,144dpi capped at4096px per side. Page rotation and
physical dimensions are returned separately, so `1to1` and aspect ratio do not
inherit raster rounding error. Poppler uses its normal opaque PDF page background;
existing PNG alpha is unchanged. Subsequent TracePicture consumes the PNG normally.

The input is snapshotted and SHA256-keyed; a fresh validated PNG is atomically
published under `<XDG_DATA_HOME>/narwhal3d/picture-assets/pdf/` (default
`~/.local/share`), including page, policy and raster digest in the filename. Different renderer output
never overwrites an earlier referenced snapshot; assets are owner-readable only. It is persistent user
data, not temp storage; deleting the PDF does not remove the placed Picture image.
Existing Picture save/reload uses this persistent asset path. This change does not
claim portable embedding of external Picture images into every document format.
Do not manually remove these assets while referenced by saved documents.

`cargo test --offline` generates its own PDF fixtures in Rust and probes actual
installed Poppler/MuPDF. `NARWHAL_PDF_FIXTURE_OUT` optionally retains those generated
fixtures for Blender integration tests. `scripts/build_linux_native.py --crate
picture_pdf` builds the helper in the existing portable offline build container.
No manufacturing surfaces or curves are approximated by this image-only feature.

## Packaged and developer routing

Default invocation requires the trusted executable-relative bundle: sibling `pdfinfo`, `pdftoppm`, `pdf-libs/`, and `pdf-data/fonts.conf`. No PATH/CWD/environment-selected executable is admitted, and an incomplete bundle never falls back to the host. Renderer environments are cleared and populated only with fixed bundle paths. The Linux helper links with `$ORIGIN/pdf-libs` RUNPATH for its own unwind dependency after supervisor environment clearing.

Developers may explicitly invoke `narwhal-picture-pdf --developer-system-poppler INPUT CACHE`; actual-host unit fixtures select this mode directly. Application calls never silently opt in. The containment/resource/page/cache/geometry policies are unchanged.

See `packaging/NOTICES.md`, `packaging/linux-pdf.lock.json`, and `licenses/PDF-Poppler-NOTICE.txt` for redistribution source/license evidence and final-package qualification commands. The Rust examples exercise real isolated Poppler, inherited renderer containment, a failing host-route control and unchanged restored output; they require explicit owned package/fixture/artifact paths and do not claim final AppImage acceptance for a staged directory.
