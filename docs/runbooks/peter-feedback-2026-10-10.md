# Peter feedback: ingestion and recording completeness

## Contracts

- A successful ingestion acknowledgement still requires the entire SQL transaction to commit. Maximum valid batches remain 5,000 readings; each SQL statement now handles at most 100 readings in the same transaction. Failed later statements roll back earlier statements and the receipt.
- SQL/password work runs outside the async event loop. Successful legacy password checks are cached for five minutes, bounded to 1,024 entries; every hit rereads current activation/hash. Rotation, revocation, deletion and tenant ownership remain enforced.
- Large or streamed request bodies queue before parsing. Ordinary reports up to 64 KiB remain concurrent. Queueing never grants an early acknowledgement or silently discards a request.
- Live events and existing alert delivery run after commit. Current WebSocket authorization/timeouts remain unchanged.
- No schema migrations, device resets, credential rotation, OTA, archive policy or deletion policy changes belong to this patch.

## Recording quality

WAV lists and upload replies gain additive completeness metadata. Gateway uploads shorter than the remaining UTC ten-minute window produce a `WAV_INCOMPLETE` server warning after durable registration. Existing WAVs remain accepted and readable.

The dashboard uses server completeness where available and a nominal-duration comparison for older readers. A 457-second file starting at a ten-minute boundary shows 173,660/228,000 samples at 380 Hz: 76.2%, with 143 seconds missing. Old metadata cannot prove whether a shorter recording came from intentional shutdown or transport loss. Unknown quality never displays green. Connection status continues to describe connectivity only.

Direct manifest quality applies to the whole segment across its runs and is labeled accordingly. Without manifest quality, short Direct files warn conservatively; the dashboard does not invent an intentional-start explanation.

## Manual firmware and later Gateway maintenance

Manual sensor firmware is published separately at https://github.com/Dinten-dev/GreenMindArdu/releases/tag/manual-2026-10-10 : Direct Production/Staging 2.8 and Gateway sensor 1.2.0. Both OLEDs show sensor ID and last-second mean/min/max in mV. Direct sampling, cloud setup, payloads, calibration and spool acknowledgement remain unchanged.

Gateway sensor setup uses the local hotspot/web form and the dashboard verification code. BLE provisioning is removed from the new manual firmware. OTA is disabled; existing sensors receive no update. Physical display, hotspot and field acceptance still require hardware.

The Raspberry Pi maintenance package is separate and is not installed in this change. It records true received/expected counts at WAV close, warns on incomplete files, preserves WAV delivery if diagnostic sidecar writing fails, preserves explicit hash acknowledgement and removes obsolete BLE worker startup. It normalizes HTTPS/WebSocket configuration to WSS. Install only in the agreed later maintenance window.

The OLED change is optional for Peter's throughput/data-loss issue. Server ingestion and completeness visibility address the server overload and missing warning. Pi maintenance is needed for accurate close-window diagnostics and the provisioning reconnect fix, but does not establish or repair the original ESP/TP-Link transport-loss cause.

## Production acceptance

Keep the existing receiver running. Build only a small offline layer on its exact image; never build or run tests on Source. Candidate memory/CPU limits and Source reserve guards must pass before start. Coordinate port 8152, SourceProbe and copier health coverage with the archive operator.

Move ingestion and WebSocket routes together only after proving no long-lived streams are stranded and recording the exact before/after proxy hashes. Preserve existing administration and archive read routes, Direct receiver, reset-response guard and original receiver image/start time. Proxy rollback must be conditional on the expected current hash and restore monitor configuration with it.

A local benchmark is not a production latency guarantee. Activation is a separate guarded operation; a merged PR does not establish that production is updated.
