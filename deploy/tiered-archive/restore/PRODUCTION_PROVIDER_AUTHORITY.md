# Explicit production provider authority

The existing `greenmind.pg15-timescale2172-source-authority.v1` remains
Staging-only. `postgres-full-v1` capture/authentication is also unchanged and
cannot target production. Existing five-part provider archives retain their
format and authenticated proof meanings.

Production schema/master/provider captures require the distinct authority
format `greenmind.pg15-timescale2172-production-authority.v1`. It contains the
existing fifteen authority fields plus exactly one `source_binding` object:

| Field | Existing production binding |
|---|---|
| `host` | `127.0.0.1` |
| `port` | `5432` |
| `role` | `greenmind_archive_reader_33fe395` |
| `role_oid`, `session_oid` | `499352` |
| `database_oid` for Gateway `plantdb` | `16384` |
| `database_oid` for Direct `greenmind_direct_production` | `301614` |

The source URL must match this complete reviewed tuple, include the existing
credential, and contain no query overrides. Neither the global source profiles
nor the production pooler configuration changes. Metadata and all three dump
processes use this same URL. `native_backup.source_url` only admits production
when the explicit provider scope is supplied; its original three-argument
interface remains Staging-only.

The root-private authority must independently pin the real source physical
identity digest, extension roster, extconfig relation OIDs/filter hashes,
complete sequence roster and all three TOCs. During the exported read-only
snapshot, actual database/actor/session OIDs and physical identity are compared
with the authority. The production login must remain unprivileged and the
transaction read-only. Production and Staging authorities cannot be relabelled
or substituted. The authority, source environment and original capture are
bound into the encrypted authentication part and checked during restore.

Restore still requires the existing explicitly authorized, empty, marked
isolated target and supervisor quarantine handshake. Production-source backup
support does not authorize restoring over production or deleting any source.

Current production preparation remains **inactive**. The native Direct
inventory proves twenty provider tables and eight provider sequences, but
`public.direct_budget_id_seq` is additionally present and lacks SELECT. All
three independently reviewed production TOC pins are also missing. Neither
missing rights nor TOCs are bypassed or fabricated by this code. Gateway has
its own outstanding schema/master access prerequisites. No staging OIDs,
TOCs, restore receipts or key recovery results become production evidence.

This patch performs no grants, DDL, profile changes, uploads, source captures,
service starts or deletion. Operators still supply and review an actual
root-owned authority before any production job.
