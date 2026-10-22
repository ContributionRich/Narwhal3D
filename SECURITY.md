# Security policy

## Supported versions

Narwhal 3D is in preview development. Security fixes are applied to the latest source and newest preview release; older previews may not receive patches.

## Report a vulnerability privately

Do not open a public issue for vulnerabilities, exposed secrets, unsafe file parsing, arbitrary code execution, path traversal, or malicious model files.

Use GitHub private vulnerability reporting:

https://github.com/ContributionRich/Narwhal3D/security/advisories/new

Include the affected version, platform, reproduction steps, impact, and a minimal safe test file when applicable. Do not include private documents or third-party confidential data.

## Scope

Security-sensitive areas include:

- model and interchange parsers;
- archive and path handling;
- external converter execution;
- update, packaging, and release scripts;
- browser/URL opening;
- autosave, recovery, and temporary files;
- native geometry and viewport dependencies;
- scripts, macros, aliases, and future extension APIs.

Narwhal never needs a GitHub token for in-app bug reporting and does not automatically upload logs, screenshots, or documents.
