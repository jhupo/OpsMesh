---
name: workspace-guide
description: "Orientation for working inside an OpsMesh project workspace: granted directories, resource usage and refusal rules."
---

# Workspace Guide

This skill orients an expert run inside one project workspace. Reading it
grants no execution rights: every script or reference file beside this
document is a resource that a run consumes only when it is granted.

## What a workspace grants

- One or more working directories the run may read and, when granted,
  write. Anything outside them does not exist for the run.
- Named files with explicit read/write/delete permissions and, when the
  runtime policy allows, use as execution input.
- Model, tool and MCP connections the project assembled for the run.

## Working rules

1. Confirm the granted directory before the first file operation.
2. Prefer the smallest change that completes the task.
3. Record what you verified; never claim a check you did not run.
4. Refuse work outside the grant and report the refusal.

## References

- `references/refusals.md` lists the refusal patterns a run must report
  instead of working around.
