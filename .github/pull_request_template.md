<!--
Title: Conventional Commits, under 70 chars. e.g. feat(store): add movements log
-->

Closes #

## Summary

<!-- What changed and why. Name the tradeoff if there was one. -->

## Test plan

<!--
Required for anything touching runtime behaviour. Not "CI passes" — numbered,
copy-pasteable steps with concrete expected outcomes. Tick each box and paste
the actual output once it passes; `gh pr edit` the body rather than leaving it
aspirational.

Pure-docs or pure-CI changes: a one-line "no runtime change; verification is CI
alone" is fine, but say it explicitly.
-->

- [ ] **1.**
  ```bash
  ```
  Expected:

- [ ] **2. CI green**

**Pass criteria:**

## Stock integrity check

<!--
The movements log is the source of truth; `quantity_on_hand` is a cache. Delete
this section only if the change genuinely cannot touch stock.
-->

- [ ] Every quantity change writes a `movement` row in the same transaction as the cache update
- [ ] `quantity_on_hand` stays rebuildable from the log
- [ ] Consumption snapshots `unit_cost_at_time` so job costing survives later price changes
