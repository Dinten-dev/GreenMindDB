"""Retain every verified feature result, including late quality revisions.

Revision ID: 0025
Revises: 0024
"""

from alembic import op

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("SET LOCAL lock_timeout='1s'")
    op.drop_constraint("uq_wav_feature_version_identity", "wav_feature_version", type_="unique")
    op.create_unique_constraint(
        "uq_wav_feature_version_identity",
        "wav_feature_version",
        [
            "wav_file_id",
            "extractor_version",
            "calibration_version",
            "parameter_hash",
            "source_sha256",
            "feature_checksum",
        ],
    )


def downgrade():
    raise RuntimeError(
        "The previous identity cannot retain distinct feature results. "
        "Keep the result history; no automatic downgrade or deletion is supported."
    )
