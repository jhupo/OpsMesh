---
name: radix-colors
description: Choose and apply Radix Colors scales for OpsMesh interfaces when designing or reviewing UI color tokens, states, and light/dark themes.
---

# Radix Colors for OpsMesh

Use the official Radix Colors scales as the palette reference for UI work:
https://github.com/radix-ui/colors

Apply this skill when a request involves palette selection, semantic color tokens,
light/dark themes, status colors, or contrast review. Keep the existing shadcn/ui
semantic token contract (`background`, `foreground`, `primary`, `muted`, `destructive`,
and related variables) as the integration boundary.

- Choose a Radix scale and step by role: low steps for surfaces, middle steps for
  borders and controls, and high steps for readable text and strong emphasis.
- Map the scale into the existing CSS variables instead of scattering raw color
  values through React components. Preserve both light and dark theme values.
- Use the project's existing icon and component system. This skill does not add a
  second component library or replace shadcn primitives.
- Check `frontend/package.json` before importing `@radix-ui/colors`. If runtime
  color constants are needed and the package is absent, add it through the
  project's `pnpm` workflow as part of the authorized UI change; otherwise use
  the existing CSS token layer.
- Verify text and controls retain accessible contrast in both themes and at the
  affected desktop and mobile widths.

The wrapper tracks the public repository at commit
`dbdb85470547c7d34b9001f48fddb08ded335979`; consult the upstream README and source
for the current scale names and package API when exact values are required.
