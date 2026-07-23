# Public Beta Checklist

## Provenance and legal

- [ ] Exact upstream commit displayed.
- [ ] AGPL license included.
- [ ] Credits and modification notice present.
- [ ] Source link accessible in UI.
- [ ] Third-party dependency/license inventory reviewed.
- [ ] No unauthorized model or detector weights bundled.

## Installation

- [ ] Clean Windows install tested.
- [ ] Upgrade from prior Studio extension tested.
- [ ] Duplicate extension detected.
- [ ] Existing model directories reused.
- [ ] User data backup and migration receipt created.
- [ ] Rollback tested.

## Correctness

- [ ] Smoke matrix passes.
- [ ] State-leak sequence passes.
- [ ] Interrupts recover.
- [ ] Hires alternate-model sequence recovers.
- [ ] ADetailer warm cache produces same detections.
- [ ] Preview Quality coherent across supported model families.
- [ ] JPEG/WebP quality states independently persist.

## Performance

- [ ] Wall timer agrees with observed wall time.
- [ ] Same-session A/B reports published.
- [ ] Preview disabled has no material overhead.
- [ ] ADetailer warm-load improvement measured.
- [ ] Hires transition improvement measured.
- [ ] No memory growth in soak.
- [ ] Compatible fallback tested.

## Support

- [ ] Support matrix published.
- [ ] Known issues published.
- [ ] Diagnostic-report instructions published.
- [ ] Security contact/process published.
- [ ] Update cadence stated.
- [ ] At least two upstream sync rehearsals completed.
