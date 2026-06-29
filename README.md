# spec-audit

**Backtest Integrity Guard for quant research codebases.**

Ensures that no parameter affecting a reported backtest result is
silently duplicated or overridden across your code. It does not
understand your strategy or your edge — it only answers one
mechanical question: *"if you changed this number, would you be
changing it everywhere it actually matters, or just in the one place
you happened to look?"*

## The 30-second example

Two files, same parameter, same name, different value:

```python
# backtest_runner.py
N = 3000  # number of bootstrap resamples used to compute the reported Sharpe CI
```

```python
# robustness_check.py
N = 2000  # someone "optimized for speed" here months later and forgot the other site
```

Nothing crashes. Nothing errors. Both files run fine in isolation. The
reported Sharpe CI and the robustness suite are now silently testing
two different things, and nothing in CI tells you that — until
`spec-audit` does:

```bash
$ spec-audit ./strategy_repo
{
  "violation_count": 1,
  "violations": [{
    "rule": "IDENTICAL_SYMBOL_DUPLICATION",
    "file": "robustness_check.py",
    "line": 3,
    "message": "constant 'N' = 2000 is also declared at backtest_runner.py:5 (value there: 3000) -- no single declaration site for this symbol"
  }]
}
```

This exact shape of bug was found during the tool's own first
dogfooding run, against an unrelated real research codebase. A
runnable version of this example lives in `demo/strategy_repo/`.

## Who this is for

Solo quants, small prop shops, and research-heavy crypto/systematic
teams running homegrown backtests where a reported Sharpe, drawdown,
or PnL number needs to survive someone asking "are you sure that's
still the number the code produces?" six months from now.

## Try it on the demo right now

```bash
pip install -e .
spec-audit demo/strategy_repo
```

## What it detects (v0 — two rules, no configuration)

### `IDENTICAL_SYMBOL_DUPLICATION`

The same constant **name** is declared as a module-level `UPPER_CASE`
assignment in more than one file. This is a symbol-identity check, not
a value check — matching by name instead of by literal value is a
deliberate design choice, made after dogfooding against a real
~50-file quant codebase showed that matching by *value* alone produces
massive false-positive noise (e.g. `TRAIL_DAYS = 30` and
`HORIZON_MIN = 30` sharing a value with no real relationship), while
still catching every real duplication found (e.g. `TAKER_BASE`
declared identically in three sibling scripts).

A hit here means: either the two sites genuinely agree today (in which
case one of them should import from the other instead of redefining
it), or they've already silently diverged — which the tool reports
exactly the same way, because divergence is the entire point: this
mechanism doesn't need to know which value is "correct," only that
there is no longer one source of truth for the name.

### `INLINE_OVERRIDE`

A function named like a guard/validator (`validate_`, `check_`,
`is_valid_`, `guard_` prefix) compares against a bare literal instead
of a named constant. This flags business-rule thresholds that are
hardcoded inline in validation logic, invisible to any constant-level
audit because they never appear as a declaration at all.

## What it explicitly does NOT do in v0

- It does not detect duplication by value (see rationale above).
- It does not detect "shadow constants" (declared but never
  referenced) — deferred until there's a usage-graph primitive worth
  building.
- It does not detect semantically-related parameters with different
  names driving the same decision path (`SEMANTIC_PARAMETER_CLUSTER`)
  — this requires AST usage-graph analysis, a harder and separate
  capability, deliberately out of scope for v0.
- It does not auto-fix anything. It does not decide which of two
  divergent definitions is correct. It only produces a report for
  human review.

## Usage

```bash
pip install -e .
spec-audit /path/to/your/repo
```

Exit code `0` if no violations, `1` if any are found — CI-friendly.

Output is JSON to stdout:

```json
{
  "violation_count": 2,
  "violations": [
    {
      "rule": "IDENTICAL_SYMBOL_DUPLICATION",
      "file": "strategy/risk.py",
      "line": 42,
      "message": "constant 'TAKER_BASE' = 0.0006 is also declared at strategy/fees.py:10 (value there: 0.0006) -- no single declaration site for this symbol"
    },
    {
      "rule": "INLINE_OVERRIDE",
      "file": "strategy/guard.py",
      "line": 15,
      "message": "guard function 'validate_margin' compares against inline literal 0.35 instead of a named constant"
    }
  ]
}
```

## CI integration example

```yaml
- name: spec-audit
  run: spec-audit .
```

A nonzero exit fails the job. Treat findings as audit items for human
review, not auto-resolved errors — the tool's job is to surface
ambiguity, not to adjudicate which declaration is correct.

## Known noise (documented honestly, not hidden)

- Differing literal *types* for the same name (e.g. an int `20` vs the
  string `"20"`) are reported as duplication even though they're a
  typing inconsistency rather than a business-logic conflict.
- The `INLINE_OVERRIDE` name-prefix heuristic can flag non-business
  helper functions that happen to start with `check_`/`validate_` but
  aren't actually validating a business rule.

Both are accepted v0 trade-offs, not silently-assumed-absent failure
modes.
