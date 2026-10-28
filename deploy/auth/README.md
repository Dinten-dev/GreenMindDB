# Invitation and session release

This release is limited to `test.green-mind.ch`. Production settings, receivers,
archive workers, retention, deletion, CSP, and tenant-role separation are outside
this release. Keep the parallel archive work on its own revision and paths.

## Version and inventory gate

Use the exact active Staging revision as the deployment base, not an unrelated
newer archive branch. The development patch and the Staging patch are separate
reviewable commits. Verify image IDs, source hashes, commands, environment names,
SQL database names, signing-key fingerprints, and proxy trust before activation.

Run `preflight.py` on the archive host. It only reads runtime metadata and uses a
read-only SQL transaction. Any ADMIN not in the configured platform-operator
allowlist blocks rollout. Do not silently change roles or the allowlist. Invitation
administration requires both the existing operator allowlist and `Role.ADMIN`.

The legacy count is scoped to the inspected Staging database. It is not a
production-fleet rotation proof. The compatibility fallback remains enabled.

## Mail configuration

Hostpoint supports SMTP over SSL on `asmtp.mail.hostpoint.ch:465` and mandatory
STARTTLS on port 587. Staging can reach port 587; port 465 timed out during the
release check. Configure `SMTP_PORT=587` and `SMTP_SECURITY=starttls` there.
Certificate verification and TLS negotiation must succeed before authentication;
there is no plaintext or automatic transport fallback. Existing configurations
default to `SMTP_SECURITY=ssl` and keep their current SSL behavior.
The sender and SMTP username are `info@green-mind.ch`. Use
`configure-staging-mail.py` for terminal-only hidden password entry. Never put
the password in chat, a command argument, a Git file, or an environment dump.
This script prepares files; it does not restart services or send mail.

The activation configuration must merge the prepared root-only `mail.env` into
the existing application environment without changing JWT, database, or storage
credentials. Mount its `smtp-password` file read-only at
`/run/secrets/greenmind-smtp-password`, readable by application UID/GID 10001.
Use `EMAIL_TRANSPORT=smtp`, `SMTP_PASSWORD_FILE` pointing to that mount, and the
explicit `STAGING_EMAIL_TO` controlled mailbox. Staging dispatch rejects every
other recipient, including verification and internal-notification recipients.
No automatic fallback sends duplicates after an SMTP failure.

`sent` records SMTP/provider acceptance, not inbox delivery. Provider failure
records `failed` and revokes the unusable token. A crash between provider acceptance
and the status commit leaves `pending`; resend replaces that token. An operator
must resolve unknown/pending outcomes rather than treating them as delivery.

## Migration and synchronized activation

1. Review the exact source diff, Staging target, checks, backup, and rollback.
2. Recheck runtime identities and tenant-admin inventory immediately beforehand.
3. Take a root-only custom-format backup of `plantdb_staging`; verify its hash
   and restore into a disposable database before the schema change.
4. Apply additive Alembic revision `0024` with the short migration lock timeout.
   Existing users, gateway credentials, verification tokens, and data are retained.
5. If a reader uses a restricted SQL role, grant only SELECT on `auth_session`.
   Session validation never updates the read-only visualization connection.
6. Switch the application and every local JWT-validating reader together. Existing
   Direct pairing/device-management delegates must authenticate through the new
   application `/auth/me`, not a stale validator that ignores `sid`.
7. Preserve `HttpOnly`, `Secure`, `SameSite=Lax`, and the existing signing secret.
   Old JWTs without `sid` intentionally stop working and require a fresh login.
8. Keep exactly the inspected proxy trust addresses. The Staging edge replaces
   incoming forwarded IP chains with the transport peer; application throttles
   never parse user-supplied proxy headers. Do not use a wildcard trust list.
9. Activate the matching frontend. Invitation links carry the token in a URL
   fragment, remove it from browser history, and offer no email override.

The development checkout contains the current ingest implementation. The older
Staging receiver already authenticates through `get_current_gateway`; its ingest
implementation is deliberately retained in the Staging candidate.

Legacy bcrypt checks reserve a bounded budget before work, including direct
re-provisioning callers. Each worker permits at most five failed/cold checks per
peer and thirty globally per minute, with one legacy scan at a time. Successful
checks refund their reservation; the bounded success cache still checks the
current database hash and activation state. This is a per-worker budget, not a
claim of a distributed rate limiter.

## Acceptance

First run `native-postgres-check.py` only against its disposable
`gm_auth_check_*` database. It checks real row locking, parallel redemption,
duplicate-email races, additive migration, read-only session validation, real
HTTP cookie/Bearer replay, idle WebSocket revocation, and synthetic legacy ingest.
It does not send email or prove the public Staging route uses the candidate.

After activation, prove the public route reaches the reviewed version. Test the
controlled mailbox request, admin listing, invitation send/failure/resend, 48-hour
expiry, email binding, owner role, login, verification compatibility, logout replay,
both WebSocket routes, anonymous protected requests, spoofed proxy headers, and
gateway ingest. Keep provider acceptance separate from human inbox confirmation.
Do not use real applicant mailboxes during Staging tests.

Readiness tooling must use private `GREENMIND_VALIDATION_TOKENS_FILE` JSON mapping
user IDs to login-issued session tokens, mode 0600. It no longer mints arbitrary
sidless user tokens. Historical archive/zone-check scripts which manually signed
JWTs must not be used unchanged against this release. Do not add a sidless bypass.

## Rollback

Keep the backup, previous source/image identities, and additive tables. Do not
drop invitation/session history, reset redemption flags, or restore the complete
database over new user/gateway data as a routine rollback.

If a regression requires the previous application, first place authentication and
protected dashboard routes into a closed maintenance response while retaining
explicit gateway ingest, WAV-upload, and Direct-upload routes. Do not reactivate
an old JWT validator publicly: that would reopen signup and resurrect otherwise
revoked sessions. Keep invitation redemption disabled until a reviewed validator
is restored. Reverting the frontend alone is permitted only with free signup still
blocked server-side. Validate gateway traffic and unauthorized rejection again.

A database restore is an offline recovery operation requiring a separate review
of writes since the backup. Normal rollback retains schema `0024`.
