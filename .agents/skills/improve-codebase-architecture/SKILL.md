---
name: improve-codebase-architecture
description: Review an existing backend codebase for architectural friction and propose scoped refactors. Use for explicit architecture assessments, recurring cross-module changes, shallow wrappers, or unclear ownership of transactions and resource lifecycles.
---

# Improve Codebase Architecture

Adapted for OpsMesh from Matt Pocock's architecture review. Local changes remove mandatory
HTML reports, extra skill dependencies, and automatic parallel-agent workflows.

Use [codebase-design](../codebase-design/SKILL.md) for the design criteria. Focus on the
user's requested area; if none is specified, use recent changes and actual maintenance
friction to select the scope.

## Examine the affected flow

Read callers, dependencies, existing architecture contracts, and relevant decisions.
Trace ownership of policy, tenant scope, state changes, transaction boundaries, event
delivery, and resource lifetime. Review the Temporal path when execution crosses it.

Look for complexity spread across callers, methods that only forward to other methods,
leaked infrastructure details, duplicated rules, and interdependent modules that change
together. Distinguish evidence of a problem from a possible improvement.

## Propose the smallest useful change

For each material finding, provide:

- Specific files and caller behavior that demonstrate the problem.
- A proposed boundary or deletion and the complexity it removes.
- The caller-facing contract and ownership of transactions, errors, and cleanup.
- Trade-offs, migration scope, and the observable checks needed to validate it.

Apply the deletion test before introducing an abstraction. Keep the existing feature-first
layout; do not add Repository, UseCase, Protocol, or separate domain models by default.
Offer alternative designs only when they expose a meaningful trade-off.

Lead with the strongest finding. Use a short explanation or diagram when helpful.
Do not create reports, ADRs, glossaries, or plans unless the task calls for them.
An assessment request authorizes assessment; an authorized implementation request can
proceed to the scoped refactor without adding an approval ritual.
