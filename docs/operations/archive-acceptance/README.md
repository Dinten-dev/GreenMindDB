# Archive acceptance and website recovery

The production website was restored on 5 November 2026 and now uses a local frontend on the production host. Public pages, static assets, backend health and storage readiness checks passed. Only frontend proxy targets changed. Some archive APIs retain their existing separate tunnel dependency.

Steps 1 and 2 are documented. Step 3 remains PARTIAL: complete native transfer/restore acceptance and capacity reserve gates remain open. The latest private candidate passed 164 local checks; its complete native transfer was not accepted within the fixed test limits. Owned probe cleanup was verified, and the affected processors are running. No source originals were deleted and no new deletion automation was enabled.

The latest read-only reserve/restore inspection confirmed the source quota contract and completed the version-metadata pagination. Local archive working-space gates remain unmet, so no full production restore was started. All 46 historical science receipts matched their local digests; one final typed-verification record remains missing. Later same-snapshot parent joins and indexes are present, while complete science/parent closure and full restore acceptance remain open.

The latest footprint measurement distinguishes current source-relation storage and retained synthetic test-workspace storage from a full restore peak. Those existing footprints were measured live, including separate logical/allocation and WAL/current-temp accounting. The earlier single reserve number was not a universal restore requirement: different verification, restore and snapshot paths have separate checks. The additional fixed margin has no documented empirical derivation in the reviewed history. No smaller reserve is approved, no limits changed, and a complete peak-measuring restore remains blocked by existing start gates. See the [footprint summary](restore-footprint-20261105.json); raw measurements and implementation remain private.

The user selected publication of summaries and checksums only. Detailed inventories, capacity measurements, implementation and operational controllers remain private; detailed publication approval is not pending.

- [Latest acceptance status](status.json)
- [Website recovery summary and checksums](website-incident-20261105.json)
- [Latest reserve and restore checks](reserve-restore-20261105.json)
- [Maintenance summary](maintenance-step3.json)
- [Dated aggregate inventory snapshots](service-inventory.json)
- [Capacity acceptance status](capacity.json)
- [Resource acceptance status](resource-limits.json)
- [Earlier isolated-test startup patch](startup-probe-v6.patch)
- [Earlier diagnostic candidate digests](startup-probe-v6-candidate.json)
- [Lifecycle policy](lifecycle-policy.json)
- [Table policy](table-policy.json)

Earlier diagnostic patches do not represent the latest private candidate or full native restore acceptance. This PR records evidence; it does not merge an integrated archive/deletion release.
