# Phase 9 — Advanced Optional Optimization

## Purpose

Explore larger gains only after the product is stable and observable.

## Candidate projects

- previous-model CPU cache;
- model prefetch into OS/page cache;
- optional two-model residency;
- background restoration;
- phase-oriented batch Hires;
- compatible ADetailer crop batching;
- conditioning reuse;
- architecture-specific Fast previews;
- smarter memory-pressure prediction.

## Requirements for each

- design document;
- memory budget;
- correctness invariant;
- explicit feature flag;
- fallback;
- support-matrix scope;
- same-session benchmark;
- soak and interrupt tests;
- independent review.

## Graduation rule

An experimental feature becomes default only when:

- it is coherent across supported model families;
- it does not increase error/OOM rates;
- it has a measurable user benefit;
- diagnostics can explain its behavior;
- users can disable it;
- upstream merges remain manageable.
