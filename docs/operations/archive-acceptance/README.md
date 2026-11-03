# Archive production acceptance

This dated record tracks the 22-step archive acceptance plan. It is an inventory and policy record, not a production release or deletion authorization.

## Step 1 — runtime inventory

Technical inventory completed on 3 November 2026. The operator owns operations and final acceptance; this chat coordinates technical work.

- Two hosts and 29 running containers were identified.
- Container images, mounted application files, service definitions, sockets, timers and configuration fingerprints were recorded.
- All 17 Nginx configuration-file hashes were independently observed again and matched the initial configuration manifest.
- Four application databases and five older test databases were separated by physical PostgreSQL identity, database OID and schema/transaction-position fingerprints.
- The archive host serves production reads and the production frontend while also hosting separate staging databases. Host location alone does not determine environment.
- The private frontend proxy destination was matched to its running container and listener.
- Gateway and Direct ingestion were assigned to their actual receiver, database and storage paths. No additional independent `DIRECT_TO_HOST` production mode was found. The local host/Pi path follows the Gateway contract.

Database fingerprints are not full database-content checksums. File and image fingerprints do not attest in-memory application code. Functional reception, complete restore, device firmware, observation periods and deletion safety require their later acceptance steps.

Private inventories and raw evidence remain with the operator. [`status.json`](status.json) records the evidence digests and public acceptance state.

## Step 2 — data lifecycle policy

In progress. Existing archive paths, account separation and encryption contracts must be preserved. Unknown provenance, active processing and quarantine block affected deletion. A policy entry or local adapter does not enable production deletion.

## Production state

No production configuration, deployment, schema, original data or existing snapshot was changed by these inventory steps. The overall archive lifecycle remains unaccepted; new WAV deletion and SQL retention remain disabled.
