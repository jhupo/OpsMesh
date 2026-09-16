# Repository Instructions

Before changing this repository, read `.codex/AGENTS.md`. It contains the project mission,
architecture invariants, dependency policy, development workflow, and validation requirements that
apply to the entire repository.

## Current code-quality rules

- The source of truth is the current code, migrations, focused tests, and architecture checks. Do
  not use an old plan, release note, or review snapshot as proof that the current checkout supports
  a behavior.
- Keep the dependency direction `api -> domains/application -> infrastructure`. Routes validate
  transport input and call services; they do not own business policy or long-running execution.
- Keep one owner for each contract, state machine, query, redaction rule, and evidence writer.
  Remove obsolete modules and update all callers directly; never add compatibility aliases,
  deprecated wrappers, re-export shims, version branches, or silent fallbacks.
- Split entrypoints such as `build`, `resolve`, `import`, and `run` into clear stages when they
  combine query, policy, serialization, and side effects. Keep cohesive state machines together;
  do not create one-line forwarding modules just to reduce line count.
- Add a package or file only for a stable contract, independent lifecycle, security boundary,
  persistence model, or replaceable adapter. Domain-specific helpers stay in their owning domain;
  `core/utils.py` is limited to small provider-neutral primitives.
- Runtime execution is explicit: `none`, `isolated`, `pooled`, or `persistent`. `none` never means
  host execution and cannot provide shell, stdio MCP, project files, or local code execution.
  Provider SDK adapters map the sandbox contract; `runtime` owns Docker/self-hosted lifecycle,
  pools, leases, staging, and cleanup.
- Documentation must describe the current tree and status/date. Delete superseded plans and stale
  paths, then update `README.md` and `docs/index.md` in the same change.
- Tests are product-flow tests: exercise the complete path from an accepted request, through
  enqueueing and durable orchestration (including team/project creation where applicable), to
  execution and the user-visible output. Keep only cross-boundary rejection, retry/recovery,
  workspace-isolation, idempotency, and redaction scenarios that are observable in that flow.
  Do not add tests for individual files, classes, dataclasses, serializers, registries, or helper
  functions, and do not create a test file solely for one implementation class. Extend an existing
  flow test when possible; remove redundant single-point tests instead of adding another layer.
- For code that is not covered by a product flow, use Ruff, type checking, import/compile checks,
  architecture checks, and `git diff --check` to catch syntax and structural regressions. For each
  functional point run only the affected flow tests plus those static checks. The complete pytest
  suite is a release-tag gate only.
- During architecture or code-organization refactors, do not add or repeatedly run tests when
  runtime behavior is unchanged. Update an existing flow test only when a changed contract makes
  it stale; otherwise rely on static checks and leave the functional-flow suite untouched.
