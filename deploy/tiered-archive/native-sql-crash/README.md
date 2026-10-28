# Native SQL crash acceptance

These helpers exercise the installed `deletion.evict`, `Source` and `Ledger`
against real PostgreSQL in one disposable database containing synthetic values.
They do not run the archive service or the operational `runtime.evict` entrypoint.

The supervisor must bind the existing PostgreSQL container identity, create a
private `test-db-admin` credential, provide the measured latency baseline and
create a random `test-binding.json`. `worker.py` requires a database name matching
`gm_tiered_accept_[a-f0-9]{24}` and records a unique ownership comment before any
test mutation. Every child independently checks that database and comment.
The exact executed supervisor is retained with the acceptance evidence.

Run only within the existing bounded helper workflow: 192 MiB RAM, 160 MiB high
watermark, zero swap, 25 percent CPU, loopback-only network access and writable
access to the new test directory only. Preserve the existing host health and
latency guards. The existing PostgreSQL container retains its separate memory
limit; no additional database server, container, account or key is provisioned.

| Case | Real exit | Required result after two retries |
|---|---:|---|
| Durable intent, before SQL DELETE | 71 | One committed DELETE |
| SQL DELETE executed, before COMMIT | 72 | Rolled back, then one committed DELETE |
| SQL COMMIT, before local completion | 73 | Reconcile absence without another DELETE |
| Local completion committed | 74 | Idempotent completed state |

`os._exit` bypasses Python and SQLAlchemy finalizers. PostgreSQL's own audit
trigger counts committed DELETEs, so a rolled-back attempt is not mistaken for
a successful deletion. A durable phase marker proves the expected crash point.
Each case also verifies retained aggregate references and unchanged local
archive bytes.

Five additional cases reject changed source values, changed organization,
25-hour-old proof, an unexpired Direct late-arrival window and a changed Direct
processing revision. None may commit a DELETE or advance beyond `verified`.

Proofs, provider objects, observation samples and clock are explicitly artificial
test fixtures. Only a child-local SQL retention acceptance setting is true; no
operational env file or activation file changes. Synthetic organization IDs,
a deliberately artificial destination and binding fingerprint prevent these
fixtures from representing real data or live acceptance. They are removed with
the disposable database after all child processes have stopped.

The proof does not replace real Storage Box/snapshot restoration, operational
writer-profile verification, a real 24-hour observation, start permission or a
separately authorized SQL pilot. Originals and operational deletion remain
protected throughout.
