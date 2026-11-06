# Step 2 — data types, storage and deletion boundaries

The classification and lifecycle specification is complete. This record does not enable production deletion or claim complete archive coverage or restore acceptance.

| Data class | Required treatment |
| --- | --- |
| Source WAVs | Retain seven full elapsed days after the first complete matching snapshot confirmation. |
| Features and dashboard history | Retain at least 90 local days; also satisfy confirmation, freshness and dependency gates. |
| Logs | Retain at least 30 local days and seven days after complete confirmation; both conditions apply. |
| Direct payload offload | Preserve its separate existing contract; no new SQL/WAV deletion authority. |
| Identity, access and assignment references | Preserve under existing exclusions. |
| Source/parent references, journals, import and retry state | Preserve for reconstruction and safe restart. |
| Permanent StorageBox archives | Preserve indefinitely. |
| Existing manual snapshots | Preserve; excluded from automatic rotation. |
| Future managed snapshots | Separate native acceptance; retain two verified own snapshots and a third creation slot. |
| Unknown or unsupported data | Preserve; unknown provenance or processing blocks deletion. |

The intended SQL scope is exactly `ingest_log`, `gateway_state_report`, `visual_reading`, `visual_wave`, `wav_feature`, `wav_feature_version` and `direct_visual_point`. These seven candidates remain on HOLD. All other 50 current relations are preserved. Raw readings (`sensor_reading`), `direct_revision`, Direct payloads and WAVs are outside this SQL scope. Historical V2 audit installation remains excluded.

The inventory resolved 62 schema foreign keys. Logical cross-database, parent, neighboring-package and reconstruction dependencies still require their full proofs. Local model/writer/adapter matches are candidates; unknown production provenance and processing remain blockers.

The earliest time is the maximum of the local retention deadline, the matching first-confirmation reserve, the late-arrival deadline and any stricter existing local retention requirement. Reverification of the same revision preserves its first confirmation; a new revision requires new evidence and reserve. Seven days means 604800 elapsed seconds, including across daylight-saving changes. Matching snapshot, readback and backup evidence must also remain at most 24 hours old. Time conditions alone never authorize deletion.

Existing paths and separate publisher/reader key bindings matched their prior private pins. The existing 32-byte backup-encryption key binding was preserved. Sensitive backups, native scientific parts and private parent/original context retain the existing AES-256-GCM block format with authenticated EOF. This does not introduce a new encryption format for WAVs. Effective provider permission checks and complete key-recovery/restore acceptance remain separate later work.

Twelve offline scope and time-boundary checks passed against the existing local policy reference. They cover the exact SQL allowlist, exclusions, seven-/30-/90-day boundaries, daylight-saving transitions, late data, changed revisions, stale evidence and active/quarantined/incomplete candidates. They do not replace native production acceptance.

No production policy, configuration, schema, original data, credentials or snapshots were changed. The full-scope seven-day reserve has not started because no complete matching full-scope snapshot confirmation has been accepted. Step 3 was not started.
