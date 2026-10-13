---
name: request-refactor-plan
description: Plan a scoped refactor from actual code, contracts, and test coverage. Use when the user asks for a refactor proposal, RFC, or incremental implementation plan; distinguish structural cleanup from behavior changes.
---

# Request Refactor Plan

Adapted for OpsMesh from Matt Pocock's retired request-refactor-plan skill.
Local changes on 2026-10-13 make interviews proportional, retain concrete code references,
and publish GitHub issues only when explicitly requested. This skill plans changes;
correctness review remains a separate engineering responsibility.

Use the following steps as needed. Existing user instructions and repository contracts
take priority. A request for a plan does not authorize implementation.

1. Extract the intended behavior and scope from the conversation. Ask concise questions
   only for missing decisions that materially change the design; state reasonable assumptions.

2. Explore the repo to verify their assertions and understand the current state of the codebase.

3. Compare alternatives only when the trade-off matters. Prefer the smallest change that
   addresses the demonstrated problem, using codebase-design for module boundaries.

4. Identify ownership of authorization, tenant scope, transactions, errors, and resource
   lifetime. Separate behavior changes and schema migrations from structural cleanup.

5. Hammer out the exact scope of the implementation. Work out what you plan to change and what you plan not to change.

6. Inspect existing tests and propose observable acceptance conditions. Use actual PostgreSQL
   for tenant isolation, transactions, locks, and concurrency. Include execution-time
   reauthorization when the change affects queued work, retries, or approval recovery.

7. Break the implementation into a plan of tiny commits. Remember Martin Fowler's advice to "make each refactoring step as small as possible, so that you can always see the program working."

8. Present the plan in chat by default. Create a document or GitHub issue only when the user
   requests that output. Scale the template below to the task; avoid generating unused drafts.

<refactor-plan-template>

## Problem Statement

The problem that the developer is facing, from the developer's perspective.

## Solution

The solution to the problem, from the developer's perspective.

## Commits

An incremental implementation sequence. Each step should leave the codebase working and
include its relevant checks. Keep inseparable migrations and caller changes together.

## Decision Document

A list of implementation decisions that were made. This can include:

- The modules that will be built/modified
- The interfaces of those modules that will be modified
- Technical clarifications from the developer
- Architectural decisions
- Schema changes
- API contracts
- Specific interactions

Reference current files and interfaces where they help make the plan concrete. Mark proposed
contracts as proposed and distinguish inspected behavior from assumptions.

## Testing Decisions

A list of testing decisions that were made. Include:

- A description of what makes a good test (only test external behavior, not implementation details)
- Which modules will be tested
- Prior art for the tests (i.e. similar types of tests in the codebase)

## Out of Scope

A description of the things that are out of scope for this refactor.

## Further Notes (optional)

Any further notes about the refactor.

</refactor-plan-template>
