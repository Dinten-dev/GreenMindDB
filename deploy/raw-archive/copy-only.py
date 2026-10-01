"""Production copy entry point. Deletion cannot be enabled by its environment."""
import os

for name in ('RAW_ARCHIVE_DELETE_ENABLED', 'RAW_ARCHIVE_READS_ACCEPTED',
             'RAW_ARCHIVE_READS_ENABLED', 'RETENTION_ENABLED', 'DIRECT_RETENTION_ENABLED'):
    os.environ[name] = 'false'

from app.raw_archive.runner import main  # noqa: E402

raise SystemExit(main())
