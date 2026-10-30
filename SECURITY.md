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

## AI permissions in current source

- Sending workspace metadata requires explicit permission. It is remembered for the configured provider/API endpoint and credential-variable name, not stored with a document or raw API key. AI Settings can forget this permission; changing recipients requires new consent. Metadata reads occur in response to chat requests, not an autonomous startup scan.
- Selected-object context accompanies each message, with explicit bounds and unavailable-state reporting. Document names and attributes are untrusted data, never authorization or executable instructions. Provider retention policies still apply.
- The in-app assistant can append one world-aligned analytic box only after a separate, one-use human approval with checked document, units, layer and idle Undo state. It cannot edit selected objects or run arbitrary command strings. The MCP transport remains read-only; this approval action is restricted to the in-app UI.
