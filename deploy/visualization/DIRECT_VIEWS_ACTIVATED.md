# Direct dashboard: Staging activation, 19 September 2026

User-approved source was published on `develop` at `bfbd525` (implementation `09e619a`). The isolated visualization services on https://test.green-mind.ch were activated at approximately 15:46 UTC. Production was not deployed.

## Verified live

- Public authenticated Direct series returned 1,018 points during the first check. Missing authentication and expired tokens returned 401; an unknown device returned 404.
- Original waveform returned 760 samples at 380 Hz in mV. CSV included resolution and coverage. A downloaded, verified PCM16 mono WAV contained 228,000 frames at 380 Hz. The newest 100 recordings were available in the seven-day listing.
- The latest received sensor block was 0.25 seconds old during validation. Actual device: `ebc9e294-6132-4c67-a935-26366bae770e`.
- Browser-style cookie authentication also passed. The 5-minute, 24-hour, 7-day and 30-day routes returned 297, 66, 84 and 9 points respectively at 15:49 UTC. Counts differ with the requested display resolution and increase as reception continues.
- The projection worker was healthy. Fourteen of sixteen source segments had been projected with zero failed segments during the follow-up check; historical processing continues in the background. Observed retained resolutions were 1 and 60 seconds. The configured older-than-seven-days resolution remains 600 seconds.
- The existing authenticated Gateway data route returned 200. A full Gateway waveform regression could not run because Staging contained no suitable verified Gateway-WAV/account combination. Local legacy compatibility tests passed before activation.
- All 17 pre-existing containers other than the two explicitly replaced visualization services were still running with identical start timestamps. The existing legacy visualization worker was included. New API, frontend and Direct projection worker had zero restarts and no OOM events; API health was healthy.

## Exact artifacts and limits

Transferred bundle SHA256: `e2141fd9e6ac422642ecd1f0e2dee25ab9872e6aad15ba55ceb41e3b680e6d3d`.

Docker's server-side config IDs are `sha256:7865c9db2bd2797887ba94359f1f0309964c9488f3a2a6d80acad24b160f466b` (API/worker) and `sha256:3e6aefaa52923bbe9fa109873375433a272c7a3f430f6a02dc408af27797652d` (frontend). These match the exported archive manifest. The local containerd image IDs in the review identify the enclosing images; every filesystem layer and the amd64 architecture matched after transfer.

No new GitHub CI run was started for this isolated release: `[skip ci]` prevented the existing automatic full-stack deployment from restarting receiving services. Locally completed checks were 56 backend tests, 24 frontend tests, type checking, lint and both amd64 builds. No GitHub CI success is claimed for these release commits.

Visual browser inspection was not performed: the browser tool could not verify its administrator-enforced policy for this site. No browser-policy workaround was used. Authenticated backend checks above were executed through the existing server validation path. The user can inspect the UI at https://test.green-mind.ch/de/app/sensors.

Configuration and rollback remain as documented in `DIRECT_VIEWS_REVIEW.md`. Use both `compose.yml` and `direct-views.yml` for targeted maintenance. Do not run the old full-stack deployment against the active visualization setup without reconciling it first.
