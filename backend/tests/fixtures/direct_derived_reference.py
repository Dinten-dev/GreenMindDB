"""Detached candidate via ORIGINAL canonical prepare_reference; SELECT only."""

import copy
from types import SimpleNamespace

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.direct.payloads import prepare_reference
from app.tiered_archive.codec import digest

PROVENANCE = "detached-original-piece-published-revision-candidate-v1"


class CandidateUnavailable(ValueError):
    pass


def require(ok, code):
    if not ok:
        raise CandidateUnavailable(code)


class ReadOnlyCanonicalCalls:
    def __init__(self, session):
        self.session = session

    def get(self, *args, **kwargs):
        return self.session.get(*args, **kwargs)

    def query(self, *args, **kwargs):
        return self.session.query(*args, **kwargs)

    def add(self, *args, **kwargs):
        raise CandidateUnavailable("canonical_candidate_add_forbidden")

    def commit(self, *args, **kwargs):
        raise CandidateUnavailable("canonical_candidate_commit_forbidden")

    def flush(self, *args, **kwargs):
        raise CandidateUnavailable("canonical_candidate_flush_forbidden")

    def delete(self, *args, **kwargs):
        raise CandidateUnavailable("canonical_candidate_delete_forbidden")


def prepare(db, row, context, checkpoint, *, session_factory=Session):
    """Returned candidate is NEVER accepted/persisted Source metadata."""
    require(context["payload_reference"] is None, "candidate_only_when_original_reference_absent")
    require(db.in_transaction(), "existing_readonly_transaction_required")
    before = digest({"row": row, "context": context})
    # Independent strict size/coverage fence before ORM fetches any manifest.
    guard = (
        db.execute(
            text("""SELECT count(*)::bigint AS pieces,
      count(r.segment_id)::bigint AS matched,
      count(*) FILTER(WHERE pg_column_size(r.manifest)>2097152)::bigint AS oversized
      FROM direct_piece p LEFT JOIN direct_segment s ON s.id=p.segment_id
      LEFT JOIN direct_revision r ON r.segment_id=s.id AND r.revision=s.published_revision
      WHERE p.chunk_id=:chunk"""),
            {"chunk": row["id"]},
        )
        .mappings()
        .one()
    )
    require(
        1 <= guard["pieces"] <= 16
        and guard["pieces"] == guard["matched"] == len(context["pieces"])
        and guard["oversized"] == 0,
        "candidate_published_piece_closure_or_size_unproven",
    )
    checkpoint()
    with session_factory(
        bind=db, autoflush=False, join_transaction_mode="rollback_only"
    ) as session:
        value = prepare_reference(ReadOnlyCanonicalCalls(session), SimpleNamespace(**dict(row)))
        require(
            not session.new and not session.dirty and not session.deleted,
            "canonical_candidate_session_writes_forbidden",
        )
        require(value is not None, "canonical_published_candidate_unavailable")
        config, reference = value
        require(
            config == context["session_config"]
            and reference.chunk_id == row["id"]
            and reference.version == 1,
            "canonical_candidate_config_or_chunk_differs",
        )
        parts = copy.deepcopy(reference.parts)
    require(db.in_transaction(), "caller_readonly_transaction_must_survive")
    require(
        before == digest({"row": row, "context": context}),
        "original_metadata_changed_during_candidate",
    )
    require(1 <= len(parts) <= 16, "bounded_candidate_parts")
    return {
        "kind": PROVENANCE,
        "chunk_id": row["id"],
        "persisted_reference_present": False,
        "accepted_reference_proven": False,
        "original_metadata_sha256": before,
        "original_payload_sha256": row["payload_sha256"],
        "original_payload_bytes": row["payload_bytes"],
        "candidate": {"version": 1, "parts": parts},
    }
