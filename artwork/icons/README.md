# Narwhal 3D icon source artwork

Copyright 2026 ContributionRich.

These 96×96 PNG source icons were generated and contributed by the Narwhal 3D repository owner. They are licensed under **GPL-3.0-or-later**, the same licence as the Narwhal 3D project code. See the repository `LICENSE` file for the full terms.

The source filenames identify their intended command, command option, panel, or future workspace action. Runtime toolbar assets use safe `narwhal-*.png` manifest names at 24×24 pixels. Run:

```bash
python3 scripts/build_toolbar_icons.py
```

The builder uses the explicit `narwhal3d/toolbar_data.py::ICON_SOURCES` mapping. It does not install an icon by guessing from a label. Source artwork not yet referenced by the runtime is retained here for future commands and UI panels.
