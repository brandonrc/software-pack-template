# Architecture decision records

An ADR records a decision that was made, the alternatives that were considered, and why the choice went the way it did. It is not a design doc and not a plan: it is written once the decision is real, and then left alone.

The point is that six months later nobody has to reconstruct the reasoning from a pull request thread, and nobody re-litigates a settled question by accident.

## When to write one

When a choice will outlive the person who made it and would be expensive to reverse. Picking a dependency you will build on, deciding how the pack handles authentication, choosing not to support a deployment mode.

Not for reversible or local choices. A function name is not an ADR.

## Naming

`YYYY-MM-DD-short-slug.md`, dated when the decision was accepted. The Nebari Operator's [`docs/decisions/`](https://github.com/nebari-dev/nebari-operator/tree/main/docs/decisions) is the reference for how these read in practice.

## Shape

Keep it short. A page is usually enough.

```markdown
# Short title of the decision

**Status:** accepted | superseded by <link>
**Date:** YYYY-MM-DD

## Context
What forced a decision. Constraints that were real at the time.

## Decision
What was decided, stated plainly.

## Alternatives considered
What else was on the table, and why it lost. This is the part future
readers actually need.

## Consequences
What this makes easy, what it makes hard, and what it commits us to.
```

## Superseding

Do not edit a decision to change its outcome. Write a new record and mark the old one superseded, pointing at the new one. The history of what was believed and when is the reason these are worth keeping.
