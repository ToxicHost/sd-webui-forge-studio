# Release, Packaging, and Update Plan

## Release artifacts

At minimum:

- source archive;
- Git tag;
- release notes;
- checksums;
- upstream base;
- patch inventory;
- installation guide;
- upgrade guide;
- rollback guide;
- known issues;
- license and credits.

A Windows convenience package may be offered, but corresponding source and control scripts remain available.

## Installer principles

- never bundle model weights by default;
- never delete user models or outputs;
- separate application code from user data;
- make the install path explicit;
- preserve portable installs;
- verify write permissions;
- back up settings before migration;
- fail safely without leaving half-migrated settings.

## Update approaches

### Git users

- fetch/pull distribution releases;
- migration scripts run by version;
- preserve local user-data paths.

### Release ZIP users

- install new program files into a new version directory;
- point to existing model/user-data directories;
- switch launcher;
- preserve prior version for rollback.

Avoid in-place overwrites of unknown local modifications without a backup.

## Versioning

Use semantic versioning for the distribution.

Also show:

```text
Forge Studio Distribution: X.Y.Z
Studio application schema: N
Forge Neo upstream: commit/hash
Studio patch set: X.Y.Z
```

## Database/settings migrations

Every migration:

- has a unique ID;
- is idempotent;
- writes a receipt;
- backs up affected files;
- can explain whether downgrade is supported;
- never silently deletes unknown fields.

## Rollback

Rollback test:

1. Install prior version.
2. Create settings/workflows/gallery state.
3. Upgrade.
4. Generate.
5. Roll back executable/source version.
6. Confirm old version can start or clearly reports schema incompatibility.
7. Restore backup if required.

## Release candidate soak

Minimum:

- several hours of mixed generation;
- repeated model swaps;
- repeated ADetailer;
- interrupt cycles;
- idle/unload behavior;
- UI tab switching;
- no unbounded memory growth;
- no stale model/prompt state.
