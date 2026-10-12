# GitHub project organization

This document defines the intended public issue and project structure. It contains no private task bookkeeping.

## Issue forms

- Bug report
- Command implementation
- UI/accessibility problem
- Performance regression
- Import/export compatibility
- Feature proposal
- Private security advisory through GitHub Security

## Labels

Use namespaced labels so issues remain searchable:

### Type

- `type: bug`
- `type: feature`
- `type: command`
- `type: documentation`
- `type: refactor`
- `type: security`

### Area

- `area: ui`
- `area: viewport`
- `area: commands`
- `area: geometry`
- `area: mesh`
- `area: subd`
- `area: drafting`
- `area: rendering`
- `area: file-io`
- `area: performance`
- `area: accessibility`
- `area: packaging`
- `area: native`

### Platform

- `platform: linux`
- `platform: wayland`
- `platform: x11`
- `platform: windows`
- `platform: macos`

### Workflow

- `status: needs-triage`
- `status: needs-info`
- `status: confirmed`
- `status: ready`
- `status: blocked`
- `status: in-progress`
- `impact: crash`
- `impact: data-loss`
- `impact: incorrect-output`
- `impact: workflow`
- `release-blocker`
- `good first issue`
- `help wanted`

## Milestones

1. Public Repository Foundation
2. Backend Coherence
3. Registered Command Completion
4. Advanced Exact Modeling
5. Mesh, SubD, Drafting, and Render Foundations
6. UI and Accessibility Baseline
7. Interchange and Manufacturing Assurance
8. Performance and Reliability Baseline
9. Linux Stable Candidate
10. Cross-platform Services
11. Windows Preview
12. macOS Preview
13. Native Application Layer
14. Stable Major Release

## Project fields

A public GitHub Project should track:

- Status
- Area
- Platform
- Milestone
- Priority
- Effort
- Risk
- Release blocker

Recommended views:

- Public roadmap
- Current milestone
- Exact geometry
- UI and accessibility
- Performance
- Platform readiness
- Release blockers
- Contributor-ready work
- Triage inbox

## Branch and review policy

- Protect `main`.
- Require public audit, compilation, focused tests, and resolved review conversations.
- Require geometry/file-format review for manufacturing-output changes.
- Require packaging review for release changes.
- Prefer squash merging and automatic contributor-branch deletion.
- Never merge unauthorized assets, secrets, private model data, or unverified generated content.
