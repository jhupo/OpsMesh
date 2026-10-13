# Consolidating shallow modules

OpsMesh adaptation of Matt Pocock's deepening guidance.

Start from a demonstrated maintenance problem. Move related behavior behind an existing
or smaller interface; avoid replacing every helper with a larger object.

- **Pure computation:** a function or cohesive module is usually enough. Verify meaningful
  inputs, results, and errors directly.
- **Database and queue interactions:** preserve transaction ownership, tenant scope,
  locks, and commit-time delivery. Use real PostgreSQL for these guarantees; in-memory
  implementations do not establish them.
- **Owned remote services:** preserve the published transport contract and failure modes.
  Add an adapter only when it hides a real transport responsibility or required variation.
- **External SDKs:** use public interfaces and native lifecycle behavior first. Mocks
  can isolate selected errors; use relevant opt-in integration checks for SDK behavior.

Test the observable contract, including rejection and failure paths affected by the change.
Remove obsolete tests only after equivalent guarantees are covered or the tested behavior
is intentionally removed. Preserve focused tests for algorithms and distinct invariants;
a broader flow test does not automatically replace them.

When reporting verification, distinguish static checks, simulated dependencies, and actual
infrastructure or SDK execution. Keep unverified integration behavior explicit.
