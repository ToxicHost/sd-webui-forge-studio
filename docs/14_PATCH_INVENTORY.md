# Neo Core Patch Inventory

This file starts empty by design. Populate it as soon as the distribution modifies Neo-owned files.

## Entry template

### PATCH-XXX — Short title

- **Neo file(s):**
- **Studio owner module:**
- **Reason:**
- **Minimal hook/change:**
- **Behavioral impact:**
- **Feature flag:**
- **Tests:**
- **Upstream conflict likelihood:** Low / Medium / High
- **Upstream PR candidate:** Yes / No / Maybe
- **Introduced in:**
- **Last reviewed against upstream:**
- **Removal criteria:**

## Patch-surface target

Before `1.0`, target approximately 8–12 directly modified Neo-owned files, excluding branding, packaging, and generated lock files.

If a feature needs edits across many core files, stop and design an adapter/hook boundary first.
