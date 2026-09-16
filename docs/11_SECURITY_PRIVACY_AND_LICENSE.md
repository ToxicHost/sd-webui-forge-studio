# Security, Privacy, and License

This document is operational guidance, not legal advice.

## License baseline

The supplied Forge Neo and Forge Studio snapshots contain AGPL-3.0 license text.

Before release:

- retain upstream notices;
- add your own copyright notices for original contributions;
- state that the project is modified from Forge Neo and its ancestors;
- include the full AGPL license;
- publish corresponding source and build/run scripts;
- expose Source, License, and Credits in the UI;
- audit every bundled third-party component separately.

## Network behavior

Default posture: local-only and no required telemetry.

Any network feature must document:

- endpoint;
- data sent;
- purpose;
- retention;
- opt-in/opt-out;
- cache behavior;
- failure behavior.

Existing hash-based metadata lookup should remain clearly opt-in and avoid filenames/prompts unless explicitly required.

## Secrets

Never include in diagnostics or repositories:

- API keys;
- tokens;
- cookies;
- remote credentials;
- private model URLs;
- signed download URLs.

## Dependency security

For releases:

- record Python dependency versions;
- scan for known vulnerabilities where tooling permits;
- identify vendored JavaScript;
- avoid executing downloaded code during update;
- verify release checksums;
- document extension trust boundaries.

## Local file security

- validate paths;
- prevent traversal in file browser/editor APIs;
- avoid serving arbitrary files outside allowed roots;
- sanitize archive extraction;
- do not trust model metadata as HTML;
- escape user-controlled labels and filenames;
- limit destructive operations to explicit user actions.

## Remote access

If users expose the app beyond localhost:

- warn that this changes the threat model;
- require authentication or a trusted reverse proxy;
- avoid permissive CORS defaults;
- protect destructive and model-load endpoints;
- document AGPL source-offer obligations for modified network deployments.

## Security response

Publish:

- private reporting contact if available;
- supported release window;
- severity triage;
- patch/release process;
- disclosure expectations.
