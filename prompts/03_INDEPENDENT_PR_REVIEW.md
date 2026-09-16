# Independent PR Review Prompt

Review this PR adversarially. Do not assume the author's design is correct.

Read the task, diff, tests, performance report, active ADRs, and patch inventory.

Check:

- output/metadata semantics;
- global-state leakage;
- interruption/error cleanup;
- active versus selected model truth;
- CUDA synchronization and stream assumptions;
- memory growth/OOM risk;
- preview concurrency/staleness;
- timer overlap accounting;
- stock UI impact;
- extension compatibility;
- upstream merge burden;
- feature flag and rollback;
- whether tests actually exercise the risky path.

For performance claims, reject evidence that:

- compares different server sessions;
- lacks fixed settings;
- omits raw logs/environment;
- adds overlapping preview time to wall time;
- reports only a best run;
- does not validate output correctness.

Return findings ranked Blocker / High / Medium / Low, then a merge recommendation.
