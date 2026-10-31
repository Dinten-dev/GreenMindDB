# Static copy failure evidence

`archive_copy_once.main` keeps its single JSON response and existing0/1/75 exit
contract. Failure responses additionally contain a version1 `diagnostic` with a
deterministic guard code, whitelisted exception class, and up to four checked
application-internal source locations. Each location contains a relative Python
file, function, line, and source-file SHA256. No absolute server paths are emitted.

The same small evidence is computed once per exception and carried unchanged
into `copy-last-run.json` and the final entry response. Existing status, deleted,
pause reasons, elapsed time and resource fields remain. Known literal pause codes
retain their values and Exit75; unrecognized dynamic pause text is replaced with
`unclassified_pause`. Unknown exception class names become `OtherError`.

No free exception arguments/messages, exception chaining, locals, SQL, object
contents, profile values, tokens or credentials are serialized. No new data is
sent to monitoring or other people. The guard code hashes only checked static
source evidence and the whitelisted class; it does not hash private messages.

A traceback filename alone is insufficient. The frame must belong to the actual
imported application module, use its matching import-spec/file path inside the
real application root, match the executing code compiled from that file, and
have a line consistent with its bytecode offset. Symlinked, external, forged,
changed or unavailable source locations are omitted. Main-module execution is
supported through its real `app.*` import spec. No source code is executed while
checking it; compilation serves only code-object comparison.

Inspection is bounded to64 traceback nodes, at most4 compiled source files of
128KiB each, and the four innermost valid frames. An overlong trace has no claimed
origin and is explicitly marked truncated. Diagnosis failures produce a fixed
unavailable code and never replace or retry the original operation. No guard,
resource limit, source lease, deletion switch or error tolerance is relaxed.

These locations are operational evidence tied to a release, not authentication
against an attacker already controlling the Python process. A guard code may
change when source bytes or the checked call path change. Detailed past errors
that the old worker never saved cannot be reconstructed by this patch.

Tests exercise a genuine existing WAV-source ArchiveBlocked guard, equivalent
guard-code stability, private dynamic messages and class names, forged actual
module paths/globals, external files, forged cache records, frame/depth bounds,
identical persisted/final evidence and known/unknown Exit75 behavior. No new
production run, admission deployment or environment change is included.
