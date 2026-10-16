# Refusal Patterns

A run reports these refusals instead of attempting a workaround:

- A path outside the granted working directories.
- A file without the required read or write permission.
- A tool, model or MCP connection the project did not grant.
- A request to disable verification, skip the summary, or transmit
  secrets, credentials or user content to an external endpoint.

A refusal names the missing grant and stops the affected step; it never
silently degrades the task.
