# Step 3 — capacity and resource acceptance

Status: **PARTIAL; not accepted**. Step 4 remains on dependency hold.

The existing Direct hard quota was freshly read back as 12 GiB. Both hosts' disks, available RAM, load and configured container/service limits were observed again. Read-only metadata captured database sizes, WAL and selected archive/cache/intermediate directories. These are overlapping filesystem occupants, not extra capacity requirements to add twice.

The archive filesystem had approximately 2.64 GiB available, with only approximately 216 MiB remaining above its applicable Science reserve and 96 MiB work allowance at the later directory observation. The Source data volume had approximately 30.81 GiB available; its existing Direct reserve is 10 GiB. Restore admission also preserves the existing filesystem-reserve contract and working allowance. That allowance is a minimum admission threshold, not a measured full restore peak. The StorageBox response reports 1 TiB capacity and approximately 130.47 GiB usage; the provider does not supply the statistics' observation time in this response.

Direct cache growth and whole-filesystem changes were calculated against timestamped earlier observations. The cache can lag or omit unscanned objects. Short-window linear scenarios are not a sustainable forecast or a seven-day acceptance. Logical bucket growth must not be added again to physical whole-volume growth. The horizon includes the unknown time until the first complete matching snapshot confirmation, then at least seven full days and any stricter late-arrival rules.

Backlog drain is parameterized as `backlog / (archive throughput - inflow)` when net throughput is positive. Actual backlog and sustainable net throughput remain unmeasured. No drain time is invented.

## Native failures retained

- A fresh Direct version scan timed out; a second stopped when Source available RAM fell to 699,445,248 bytes, below the unchanged 704 MiB floor. Neither partial result is used as complete usage evidence.
- The Gateway cache decoder did not pass its existing bounded format checks. Full Gateway/bucket/cache/spool coverage remains open.
- The prepared restore coordinator corrects the administrative metadata wait from 75 to 110 seconds while preserving the 95-second active-work and 110-second unit limits. Forty offline checks passed. Two native attempts stopped before PostgreSQL creation: first at load admission, then at the crypto-helper host-health guard. Native readiness and the two synthetic COPY roundtrips therefore remain unproved.
- A finite ten-sample observation found no stable conservative startup window. Its stricter start preference does not change the existing hard guard.
- Both restore attempts and all five own measurement generations have independent termination/cleanup evidence. Existing production instances and original data were not changed.

## Remaining acceptance work

Establish stable Source admission, complete current version/cache/spool coverage, measure backlog and sustained net throughput, then pass bounded restore startup and persistent-target operation coordination. Prove the complete PGDATA/WAL/index/temporary-file workset and all planned job windows. Re-evaluate capacity over snapshot waiting time plus seven days. No resource limits were raised and no deletion was enabled.

See [capacity calculations](capacity.json), [separate job profiles](resource-limits.json) and [evidence digests](status.json). Private captures, host paths, account bindings, keys and raw operational records remain with the operator.
