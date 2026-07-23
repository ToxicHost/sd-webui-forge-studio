# Definition of Done

A change is done only when all applicable items are true.

## Code

- narrowly scoped;
- error paths handled;
- cleanup idempotent;
- no unexplained global-state mutation;
- feature flag or compatibility fallback for risky behavior;
- type/schema boundaries updated;
- no model/user data committed.

## Tests

- relevant unit/integration tests pass;
- GPU smoke test performed when behavior touches generation;
- state-leak sequence considered;
- interruption considered;
- stock UI impact considered;
- output/metadata parity checked.

## Performance

For a performance claim:

- baseline and variant run in same session;
- fixed seed/settings;
- environment captured;
- raw logs retained;
- wall timer validated;
- output correctness validated;
- median/repetitions reported;
- uncertainty stated.

## Documentation

- project state updated;
- phase/backlog updated;
- API/behavior docs updated;
- patch inventory updated for Neo core edits;
- ADR added when architecture/policy changes;
- migration/release notes added if user-facing.

## Review

- diff reviewed by someone/model other than the author for risky lifecycle/CUDA changes;
- reviewer can explain why the change is safe;
- rollback or disable path documented;
- no unresolved blocker comments.
