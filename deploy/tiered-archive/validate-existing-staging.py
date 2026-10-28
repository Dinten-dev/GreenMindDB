"""Validate reuse of the inventoried local Staging stack, without provisioning it.

This deliberately separate path does not weaken install-staging.py's isolated
copy contract. It accepts the already reviewed Box account and RO subaccount,
keeps source credentials read-only, and never authorizes service activation.
"""
import re
from urllib.parse import urlsplit

FLAGS = ('TIERED_ARCHIVE_DELETE_ENABLED', 'RAW_ARCHIVE_DELETE_ENABLED',
         'RETENTION_ENABLED', 'DIRECT_RETENTION_ENABLED', 'VISUAL_PRUNE_ENABLED',
         'TIERED_ARCHIVE_SQL_RETENTION_ACCEPTED',
         'TIERED_ARCHIVE_DIRECT_LOCATORS_ACCEPTED', 'TIERED_ARCHIVE_FEATURE_READS_ACCEPTED')
STATE = '/var/lib/greenmind/tiered-archive/staging'
BACKEND = '/opt/greenmind/tiered-archive/overlays/staging-snapshots-cdc65fbaf137/backend'
CLIENT_DIR = '/opt/greenmind/tiered-archive/clients/postgresql-15.10-55bb10b9c0ca'
CLIENT_MANIFEST = '55bb10b9c0cac4be01ef2d14ca03e51e3ccd837fb7ec83c2a46d4359d4b0e6f0'
CREDENTIAL_FIELDS = ('RAW_ARCHIVE_SFTP_KEY', 'RAW_ARCHIVE_SFTP_KNOWN_HOSTS',
                     'RAW_ARCHIVE_READ_ONLY_SFTP_KEY', 'RAW_ARCHIVE_READ_ONLY_SFTP_KNOWN_HOSTS',
                     'CREDENTIALS_DIRECTORY', 'TIERED_ARCHIVE_LATENCY_BASELINE',
                     'TIERED_ARCHIVE_SNAPSHOT_TOKEN_FILE')


def validate(writer, reader, schedule):
    if (writer.get('TIERED_ARCHIVE_PG_CLIENT_DIR') != CLIENT_DIR or
            writer.get('TIERED_ARCHIVE_PG_CLIENT_MANIFEST_SHA256') != CLIENT_MANIFEST):
        raise ValueError('Reviewed native PostgreSQL client runtime required')
    for profile in (writer, reader):
        # EnvironmentFile overrides Environment=, including systemd %d credentials.
        if profile.get('PYTHONPATH') != BACKEND:
            raise ValueError('Profile must select the exact reviewed runtime overlay')
        if any(key in profile for key in CREDENTIAL_FIELDS):
            raise ValueError('Credential paths must come from systemd, not EnvironmentFile')
        for key in ('RAW_ARCHIVE_ENVIRONMENT', 'TIERED_ARCHIVE_ENVIRONMENT'):
            if profile.get(key) != 'staging':
                raise ValueError('Explicit staging environment required')
        if any(profile.get(key) != 'false' for key in FLAGS):
            raise ValueError('All deletion and acceptance gates must stay false')
        if any('WRITER_' in key or 'FILL_' in value for key, value in profile.items()):
            raise ValueError('No source writer credentials or unfinished bindings')
        if profile.get('TIERED_ARCHIVE_STATE_DIR') != STATE:
            raise ValueError('Reuse existing isolated local state')
        if profile.get('RAW_ARCHIVE_STATE_DIR') != STATE + '/raw':
            raise ValueError('Separate staging raw receipt state required')
    for profile in (writer, reader):
        if profile.get('RAW_ARCHIVE_HEALTH_URLS') != 'http://127.0.0.1:8001/health,http://127.0.0.1:8002/health,http://127.0.0.1:9002/minio/health/ready':
            raise ValueError('Writer and reader must check the existing Staging APIs')
        if profile.get('RAW_ARCHIVE_WORKER_ROLE') != 'existing-staging' or profile.get('RAW_ARCHIVE_SOURCE_PROBE_URL'):
            raise ValueError('Only local Staging health checks are allowed')
        if profile.get('RAW_ARCHIVE_SOURCE_MOUNTS') != STATE:
            raise ValueError('Existing Staging partition must be checked')
        if int(profile.get('RAW_ARCHIVE_MIN_AVAILABLE_MEMORY_MIB', '0')) < 1024:
            raise ValueError('Keep host memory reserve in both services')
        if not 0 < float(profile.get('RAW_ARCHIVE_MAX_HOST_LOAD', '0')) <= 2:
            raise ValueError('Keep host load ceiling in both services')
        if not 0 < float(profile.get('RAW_ARCHIVE_MAX_LOAD_PER_CPU', '0')) <= .5:
            raise ValueError('Keep normalized load ceiling in both services')
        if not re.fullmatch('[0-9a-f]{64}', profile.get('TIERED_ARCHIVE_LATENCY_BASELINE_SHA256', '')):
            raise ValueError('Measured pinned latency baseline required')
    if writer['TIERED_ARCHIVE_LATENCY_BASELINE_SHA256'] != reader['TIERED_ARCHIVE_LATENCY_BASELINE_SHA256']:
        raise ValueError('Writer and reader baseline binding differs')
    if writer.get('TIERED_ARCHIVE_SOURCE_ORIGIN') != 'existing_archive_host_staging_copy':
        raise ValueError('Inventoried source origin required')
    if writer.get('RAW_ARCHIVE_WORKER_ROLE') != 'existing-staging':
        raise ValueError('Explicit reused staging role required')
    if writer.get('RAW_ARCHIVE_SOURCE_PROBE_URL'):
        raise ValueError('No production telemetry or source access')
    for source, database, bucket in (
        ('GATEWAY', 'plantdb_staging', 'greenmind-raw'),
        ('DIRECT', 'greenmind_direct_staging', 'greenmind-direct-staging-hotspot'),
    ):
        url = urlsplit(writer.get('RAW_ARCHIVE_' + source + '_DATABASE_URL', ''))
        if (url.scheme, url.hostname, url.port, url.path, url.username) != (
            'postgresql+psycopg2', '127.0.0.1', 5433, '/' + database,
            'greenmind_archive_staging_ro',
        ) or not url.password:
            raise ValueError('Only the inventoried local read-only database binding')
        if url.query or url.fragment:
            raise ValueError('No hidden connection overrides')
        backup_key = 'TIERED_ARCHIVE_' + source + '_BACKUP_DATABASE_URL'
        if backup_key in writer and writer[backup_key] != writer['RAW_ARCHIVE_' + source + '_DATABASE_URL']:
            raise ValueError('Backup must reuse the exact existing read-only Staging source')
        if writer.get('RAW_ARCHIVE_' + source + '_S3_ENDPOINT') != 'http://127.0.0.1:9002':
            raise ValueError('Only the existing local staging object store')
        if writer.get('RAW_ARCHIVE_' + source + '_S3_BUCKET') != bucket:
            raise ValueError('Only the inventoried bucket')
        if writer.get('RAW_ARCHIVE_' + source + '_S3_ACCESS_KEY') != 'gm-tiered-staging-ro':
            raise ValueError('Explicit restricted staging object reader required')
        if not writer.get('RAW_ARCHIVE_' + source + '_S3_SECRET_KEY'):
            raise ValueError('Missing restricted object reader credential')
    expected = {
        'RAW_ARCHIVE_SFTP_USER': 'u676312',
        'RAW_ARCHIVE_SFTP_HOST': 'u676312.your-storagebox.de',
        'RAW_ARCHIVE_SFTP_ROOT': 'greenmind-raw',
        'RAW_ARCHIVE_HEALTH_URLS': 'http://127.0.0.1:8001/health,http://127.0.0.1:8002/health,http://127.0.0.1:9002/minio/health/ready',
    }
    if any(writer.get(k) != v for k, v in expected.items()):
        raise ValueError('Existing destination and real source health bindings required')
    if int(writer.get('RAW_ARCHIVE_MIN_AVAILABLE_MEMORY_MIB', '0')) < 1024:
        raise ValueError('Keep host memory reserve')
    if not 0 < float(writer.get('RAW_ARCHIVE_MAX_HOST_LOAD', '0')) <= 2:
        raise ValueError('Keep host load ceiling')
    if not 0 < float(writer.get('RAW_ARCHIVE_MAX_LOAD_PER_CPU', '0')) <= .5:
        raise ValueError('Keep normalized load ceiling')
    for key, ceiling in [('TIERED_ARCHIVE_MAX_WAV_BUNDLE_BYTES', 5 * 1024**2),
                         ('TIERED_ARCHIVE_MAX_SQL_PAGE_BYTES', 1024**2)]:
        if not 0 < int(writer.get(key, '0')) <= ceiling:
            raise ValueError('Keep bounded copy sizes')
    if any(k.startswith('RAW_ARCHIVE_SFTP_') or 'DATABASE_URL' in k for k in reader):
        raise ValueError('No upload/database access in reader')
    if reader.get('RAW_ARCHIVE_READ_ONLY_SFTP_USER') != 'u676312-sub1':
        raise ValueError('Reuse provider-enforced read-only subaccount')
    if reader.get('RAW_ARCHIVE_READ_ONLY_SFTP_HOST') != 'u676312-sub1.your-storagebox.de':
        raise ValueError('Matching read-only hostname required')
    if 'RAW_ARCHIVE_READ_ONLY_SFTP_ROOT' in reader:
        raise ValueError('Existing account is already rooted at greenmind-raw')
    if writer.get('TIERED_ARCHIVE_BACKUP_KEY_SHA256') != reader.get('TIERED_ARCHIVE_BACKUP_KEY_SHA256'):
        raise ValueError('Key recovery binding differs')
    if schedule.get('schema') != 1 or schedule.get('environment') != 'staging':
        raise ValueError('Staging schedule required')
    if schedule.get('backups') or schedule.get('snapshot_id') or schedule.get('scheduled_snapshots'):
        raise ValueError('First copy phase excludes inline backups and snapshots')
    if any(v not in ('gateway', 'direct') for v in schedule.get('wav_sources', [])):
        raise ValueError('Unknown WAV source')
    return {'validated': True, 'source_ports': [5433, 9002],
            'destination_prefix': 'greenmind-raw/staging/', 'deletion_enabled': False,
            'activation_authorized': False}


def validate_restored(writer, reader, schedule, evidence, *, now=None):
    """Bind a separately verified restore; never authorize service start/deletion.

    The installing operator must load evidence from its hash-pinned, private,
    root-owned proof file. This pure validator does not grant that trust itself.
    The initial validate() contract remains unchanged for first-copy installs.
    """
    import uuid
    from datetime import UTC, datetime, timedelta

    def stamp(value):
        result = datetime.fromisoformat(value)
        if result.tzinfo is None:
            raise ValueError('Timezone-aware restore evidence required')
        return result.astimezone(UTC)

    def require(condition):
        if not condition:
            raise ValueError('Complete matching restore-bound Staging evidence required')

    current = now or datetime.now(UTC)
    require(current.tzinfo is not None)
    # Reuse every existing endpoint, identity, resource and privilege guard.
    validate(writer, reader, {**schedule, 'snapshot_id': None})
    require(all(p.get('TIERED_ARCHIVE_READS_ENABLED') == 'true' for p in (writer, reader)))
    snapshot = evidence.get('snapshot', {})
    require(evidence.get('schema') == 1 and evidence.get('environment') == 'staging')
    require(evidence.get('phase') == 'restore_verified_configuration')
    require(evidence.get('runtime_sha256') == 'cdc65fbaf137e8e244d4e760b7de62a13970dd8b6556b80be8e8063b5b6d6697')
    require(evidence.get('activation_authorized') is False and evidence.get('deletion_enabled') is False)
    require(evidence.get('existing_receipts_preserved') is True)
    require(evidence.get('box_writes') == 0 and evidence.get('source_writes') == 0)
    require(evidence.get('historical_snapshot_restore_passed') is True)
    require(type(snapshot.get('id')) is int and snapshot['id'] > 0)
    require(snapshot.get('storage_box') == 658321 and schedule.get('snapshot_id') == snapshot['id'])
    require(bool(re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}', snapshot.get('name', ''))))
    require(schedule.get('scheduled_snapshots') is False and not schedule.get('backups'))
    verified = stamp(evidence['verified_at'])
    require(stamp(snapshot['created']) <= verified <= current)
    require(current - verified <= timedelta(hours=24))
    require(current - stamp(snapshot['created']) <= timedelta(hours=24))
    require(bool(re.fullmatch('[0-9a-f]{64}', evidence.get('bindings_sha256', ''))))
    require(bool(re.fullmatch('[0-9a-f]{64}', evidence.get('previous_ledger_sha256', ''))))
    archives = evidence.get('sql_archives', [])
    require(isinstance(archives, list) and 0 < len(archives) <= 256)
    require(evidence.get('sql_bundles_added') == len(archives))
    require(len({a.get('identity') for a in archives}) == len(archives))
    scopes = schedule.get('scopes', [])
    require(isinstance(scopes, list) and len(scopes) <= 256)
    require(evidence.get('wav_sources') == schedule.get('wav_sources'))
    for archive in archives:
        require(bool(re.fullmatch('[0-9a-f]{64}', archive.get('identity', ''))))
        require(bool(re.fullmatch('[0-9a-f]{64}', archive.get('manifest_sha256', ''))))
        require(type(archive.get('rows')) is int and 0 < archive['rows'] <= 500)
        uuid.UUID(archive['organization'])
        require(archive.get('source') == ('direct' if archive['dataset'] == 'direct_visual_point' else 'gateway'))
        require(archive['dataset'] in ('visual_reading', 'visual_wave', 'direct_visual_point'))
        require(archive.get('current_readback_verified') is True)
        require(archive.get('snapshot_readback_verified') is True)
        require(archive.get('source_matches_current') is True)
        require(archive.get('state') == 'verified')
        require(archive.get('dependencies_complete') is False)
        require(stamp(archive['start']) < stamp(archive['end']))
        require(stamp(archive['published_at']) <= stamp(snapshot['created']))
        require(stamp(snapshot['created']) <= stamp(archive['verified_at']) <= verified)
        require(any(
            scope.get('dataset') == archive['dataset']
            and scope.get('organization') == archive['organization']
            and stamp(scope['start']) <= stamp(archive['start'])
            for scope in scopes
        ))
    return {'validated': True, 'phase': 'restore_verified_configuration',
            'source_ports': [5433, 9002], 'snapshot_id': snapshot['id'],
            'sql_bundles': len(archives), 'deletion_enabled': False,
            'activation_authorized': False}
