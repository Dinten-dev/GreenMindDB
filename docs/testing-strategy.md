# Testing strategy

The executable commands, CI versions, fixtures, and integration-test instructions are maintained
in [testing.md](testing.md). This file records the principles behind those checks without
duplicating a command reference.

## Priorities

1. Protect authentication, authorization, tenant isolation, and device trust boundaries.
2. Preserve measurement identity and data integrity across retries.
3. Exercise validation at HTTP, WebSocket, gateway, database, and object-storage boundaries.
4. Keep ordinary unit tests deterministic and fast.
5. Use integration tests for behavior SQLite or jsdom cannot reproduce.

## Test layers

| Layer | Purpose | Typical tool |
|---|---|---|
| Unit | Pure validation, parsing, serialization, and service behavior | pytest or Jest |
| API/component | Observable request, cookie, authorization, or UI behavior | FastAPI `TestClient` or React Testing Library |
| Integration | PostgreSQL/TimescaleDB, Alembic, MinIO, and container networking | pytest with Docker Compose |
| Cross-repository | Sensor, gateway, and server protocol compatibility | GreenMindArdu + GreenMindRPI + GreenMindDB staging setup |

Any change to a sensor payload, gateway API, measurement identifier, WAV format, OTA contract,
or database schema needs consumer-aware compatibility testing across the three repositories.

## Required cases

For each externally visible feature, cover the normal path and the important rejection paths:

- missing, malformed, oversized, stale, replayed, and duplicate input;
- unauthenticated, wrong-tenant, and wrong-role access;
- transient dependency failure and bounded retry behavior;
- partial writes, restart recovery, and idempotent resubmission where applicable;
- boundary values for timestamps, identifiers, packet sizes, and resource limits.

Coverage is a regression signal, not a substitute for these cases. The backend CI threshold is
60%; new tests should be chosen for risk reduction rather than line-count inflation.
