# One-off existing-admin schema bridge

This operator uses the **same existing OpenSSH identities and host-specific sudo
files as `admin_probe.py`**. That wrapper uses OpenSSH, not Paramiko. Its text
decoder is not used for binary schema data. There is no new key, account, token,
SSH authorization, daemon, port, persistent server configuration or dependency.
`greenmind-archive-link` and its `/bin/false` restriction remain untouched.

`controller.py` defaults to an inert response. An executable scope requires an
explicit short-lived authorization, fresh run UUID and pinned provider/physical
source/transport hashes. The provided scope draft is inactive and deliberately
has no fabricated production authority hashes.

## Connection to the existing capture

The Root backup operator creates a **new dedicated empty Root0700 directory**
inside its existing private state root. It supplies
`AdminResponseHandoff(directory)` as the existing
`SourceSchemaCapture.private_response` callback, with the frozen source-program
SHA and transport-binding SHA from the bridge manifest. The existing public
`schema_provider.capture(..., schema_capture=hook)` seals/encrypts the result.

The callback publishes only the existing schema request: exported snapshot,
source/authority/physical identity hashes and fixed schema-only command. The Mac
controller fetches that small metadata request over the existing Archive admin
channel, starts a bounded Archive Root receiver and then a bounded Main Root
source worker. The Main worker verifies the exact machine, existing PostgreSQL
container/image/generation, database OID, PG version and postmaster timestamp.
It uses the existing `POSTGRES_USER` **inside that container**, never exporting
its credential. The caller's read-only repeatable-read exported snapshot is
passed unchanged to `pg_dump --schema-only --format=custom`.

Binary bytes go Source Root stdout → encrypted existing SSH → bounded Mac pipe
buffers → encrypted existing SSH → Archive Root0600 file. They never go to Mac
disk, logs, Chat output or normal admin-probe text decoding. Source and receiver
independently hash/count the complete stream. Only exact source process wait0,
source generation/cgroup disappearance, receiver wait0 and receiver terminal
proof permit final private receipt publication. Existing hook FD/mode/hash/TOC
verification still runs afterward. Existing AES encryption remains on Archive;
the AES key is never supplied to Main or Mac by this bridge.

## Bounds and failure meaning

Host transport workers each have 64 MiB maximum, 56 MiB high, 5% CPU and zero
swap. Main additionally retains the 800 MiB host-memory and load≤2.0 gates and
fixed loopback receiver health checks. The Docker-exec client itself runs in the
existing PostgreSQL container, outside the host helper cgroup: its **own** child
gets a 128 MiB address-space limit, nice19 and an existing container `timeout`
utility with at most 45 seconds, then three seconds stop grace. No PostgreSQL
container or original backend limit is changed. Missing timer support fails
closed. Source metadata queries retain eight-second/150ms SQL limits; these
are not misrepresented as a whole-`pg_dump` deadline.

The binary stream is capped at 64 MiB with 64 KiB frames. It never authorizes
table DATA, master rows, SQL statements supplied by a requester or another DB.
The source uses a unique `PGAPPNAME=gm-schema-source-<runUUID>` for bounded
post-failure investigation. Docker CLI termination alone is **not** treated as
proof that its in-container child ended. Every failure blocks publication and
automatic retry, retains private partial evidence and reports the PG outcome
unknown; the operator must establish that exact own child's/backend's terminal
state before any new attempt. No original backend is cancelled or killed.

## Remaining production prerequisites

This is a supervised Mac-assisted one-off backup proof, **not unattended
server-to-server production architecture**. A permanent least-privilege channel
remains separate work.

Gateway producer `backup.dump` checks all master-table rights **before** it
exports the snapshot or invokes this schema hook. Therefore the five missing
Gateway master SELECTs still block the full producer:
`alembic_version`, `form_submission`, `plant`, `plant_observation_access`,
`plant_sensor_assignment`. This bridge removes neither those checks nor their
permission requirement. It only provides the schema-only transport and does
not make a complete Gateway backup executable.

The fastest existing-code route for master reads is exactly those five reader
grants **if separately authorized**; the latest three Direct-table consent does
not authorize them. Avoiding shared-reader grants would require an explicitly
separate typed SourceMasterCapture plus source-row/count/content proof and
declared-principal binding. It could reuse these private pipes, but is not
silently enabled by changing `--schema-only` or by reusing this request format.

No native bridge run, production backup, restore, snapshot acceptance or
original deletion is claimed by this implementation.
