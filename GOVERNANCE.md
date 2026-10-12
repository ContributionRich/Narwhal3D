# Governance

Narwhal 3D is currently maintained by the repository owner with community contributions reviewed through GitHub.

## Decision priorities

1. Manufacturing correctness and data safety.
2. Honest behavior and explicit limitations.
3. Independent implementation and asset provenance.
4. Regression coverage and reproducible evidence.
5. Accessibility, performance, and maintainability.
6. Breadth of features.

Accuracy takes priority over convenience. A missing exact operation should remain unavailable rather than silently producing unsuitable geometry.

## Changes

- Small fixes use focused pull requests with tests.
- New commands begin with a public behavior and geometry contract.
- Architecture changes should begin as an RFC issue describing alternatives, compatibility, migration, and validation.
- Geometry, file-format, release, and security changes require additional review appropriate to their risk.
- Maintainers may reject changes that copy unauthorized material, weaken tests, hide limitations, or compromise manufacturing geometry.

## Releases

Preview releases may contain incomplete features identified in `STATUS.md`. Stable releases require the gates in `ROADMAP.md`.

## Community roles

As the contributor base grows, maintainers may delegate triage and review for UI/accessibility, commands, geometry/file formats, platform/release, documentation/community, and security. Ownership routes review; it does not prevent contributions outside a person’s usual area.
