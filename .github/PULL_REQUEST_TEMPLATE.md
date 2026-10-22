## Summary

Describe the user-visible behavior and keep the pull request focused.

Closes #

## Validation

- [ ] Added or updated focused tests
- [ ] Deliberately broke the new behavior and observed the focused test fail
- [ ] Restored the implementation and observed the focused test pass
- [ ] Ran the relevant broader test or UI gate
- [ ] Ran `git diff --check`

## Impact review

- [ ] Unsupported behavior remains explicit and does not silently fall back
- [ ] Manufacturing geometry remains exact; viewport tessellation is display-only
- [ ] Save/reopen and Undo/Redo behavior are covered when document state changes
- [ ] UI changes include keyboard, focus, scaling, overflow, and accessibility consideration
- [ ] Platform or packaging effects are documented
- [ ] No private data, secrets, unauthorized assets, generated evidence, or unreviewed dependencies are included
- [ ] Documentation and `CHANGELOG.md` are updated when appropriate

## Screenshots or reports

Attach only privacy-reviewed, redistributable evidence.
