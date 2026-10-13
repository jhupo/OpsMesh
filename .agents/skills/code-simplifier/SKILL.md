---
name: code-simplifier
description: Simplify recently changed Python code for clear naming, readable control flow, and fewer redundant abstractions while preserving behavior. Use for targeted cleanup or a readability pass after an implementation.
---

# Code Simplifier

Adapted from Anthropic's code-simplifier agent. Modified for OpsMesh on 2026-10-13:
Python replaces React and TypeScript guidance, scope stays task-specific, and verification
follows repository contracts. See LICENSE for the upstream Apache-2.0 license.

Read the changed code and its callers before editing. Focus on the authorized scope.
Preserve outputs, errors, ordering, side effects, authorization, transaction boundaries,
cancellation, retries, and resource lifetime.

## Simplify what readers must understand

- Name values after their domain meaning and functions after their actual operation.
  Replace vague names when caller context does not make their meaning clear.
- Remove unnecessary nesting, dead branches, redundant state, obvious comments, and
  wrappers that merely forward arguments without enforcing a useful contract.
- Keep related decisions together. Consolidate duplicate policy when it is genuinely
  the same rule; similar syntax alone is insufficient.
- Prefer explicit Python control flow to dense expressions. Optimize readability,
  not line count.
- Use existing types, SDK methods, and dependencies. Introduce a class for coherent
  behavior or lifecycle ownership, not to attach Service, Manager, or Handler to a name.
- Follow the existing Ruff and strict mypy settings. Preserve SDK overrides,
  serialized fields, schema names, and migration contracts unless changes are requested.

Keep helpers and abstractions that hide real complexity. When a cleanup would change
business behavior or architecture, describe that change separately and stay within the
task's authorization.

Verify affected callers and observable behavior using proportionate existing checks.
Summarize only changes that matter to understanding or maintenance.
