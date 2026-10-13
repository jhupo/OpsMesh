---
name: codebase-design
description: Design Python module interfaces and responsibilities, remove shallow wrappers, and decide whether an abstraction earns its keep. Use when adding a module, refactoring tangled code, or evaluating coupling and encapsulation.
---

# Codebase Design

Adapted for OpsMesh from Matt Pocock's codebase-design. Local changes remove
mandatory terminology and parallel-design workflows; repository contracts take priority.

Design modules that hide substantial complexity behind a small, understandable interface.
A module can be a function, class, or feature package; its size and suffix do not prove quality.

## Start with the current flow

Read the affected callers, implementation, and contracts. State the concrete change or
friction: duplicated policy, leaked state, scattered changes, or a caller that must know
too much. Work within the existing feature rather than introducing global technical layers.

Prefer deleting dead code, calling an SDK directly, or using an existing function before
introducing another class or interface.

## Decide whether the boundary earns its keep

- **Deletion test:** imagine removing a wrapper and updating its callers. If complexity
  disappears, the wrapper is probably unnecessary. If policy or coordination spreads
  across callers, the boundary may be useful.
- **Small interface, real responsibility:** callers should not need to know internal
  ordering, database details, or SDK bookkeeping. Include errors, authorization,
  transaction ownership, retries, and resource lifetime in the interface contract.
- **Keep related invariants together:** extracting a helper is useful when it hides a
  coherent responsibility. Avoid splitting a transaction or workflow into trivial
  forwarding methods to meet a line-count target.
- **Abstract an actual variation:** use SDK interfaces first. Introduce a Protocol or
  adapter for a concrete substitution, external boundary, or independently owned
  responsibility. A mock alone does not justify another production interface.
- **Prefer composition when it reduces coupling:** functions and concrete dependencies
  are sufficient for many cases. Use inheritance for a real subtype or SDK extension.

## Name the responsibility

Use established domain terms and specific operations. A verb-led function often expresses
a one-off operation better than a new object. Use a class when it owns related behavior,
state, or a resource lifecycle.

A suffix such as Service, Manager, or Handler is neither a requirement nor an automatic
defect. Explain what the object owns. Renaming a pass-through does not make it an abstraction.
Keep SDK names and external contracts stable unless changing them is part of the task.

## Verify the proposed change

Explain what complexity the boundary hides, which callers become simpler, and where
authorization, transactions, and resource release live. Scale the explanation to the change;
a small refactor needs no new design document.

Read [DEEPENING.md](DEEPENING.md) when consolidating multiple modules or changing an I/O
boundary. Verify behavior through the affected interface and product flow, retaining
tests that prove distinct guarantees.
