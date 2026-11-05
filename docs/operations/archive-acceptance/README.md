# Archive acceptance and website recovery

The production website was restored on 5 November 2026 and now uses a local frontend on the production host. Public pages, static assets, backend health and storage readiness checks passed. Only frontend proxy targets changed. Some archive APIs retain their existing separate tunnel dependency.

Steps 1 and 2 are documented. Step 3 remains PARTIAL: complete native transfer/restore acceptance and capacity reserve gates remain open. The latest private candidate passed 164 local checks; its complete native transfer was not accepted within the fixed test limits. Owned probe cleanup was verified, and the affected processors are running. No source originals were deleted and no new deletion automation was enabled.

The user selected publication of summaries and checksums only. Detailed inventories, capacity measurements, implementation and operational controllers remain private; detailed publication approval is not pending.

- [Latest acceptance status](status.json)
- [Website recovery summary and checksums](website-incident-20261105.json)
- [Maintenance summary](maintenance-step3.json)
- [Dated aggregate inventory snapshots](service-inventory.json)
- [Capacity acceptance status](capacity.json)
- [Resource acceptance status](resource-limits.json)
- [Earlier isolated-test startup patch](startup-probe-v6.patch)
- [Earlier diagnostic candidate digests](startup-probe-v6-candidate.json)
- [Lifecycle policy](lifecycle-policy.json)
- [Table policy](table-policy.json)

Earlier diagnostic patches do not represent the latest private candidate or full native restore acceptance. This PR records evidence; it does not merge an integrated archive/deletion release.
