"""Standalone worker configuration; deliberately outside API/Compose startup."""

import hashlib
import json
import os
import shutil
from contextlib import ExitStack
from pathlib import Path
from urllib.parse import urlparse

from .daily import Limits, run_daily
from .policy import ArchiveBlocked
from .health import HealthProbe, SafetyPause
from .telemetry import Metrics
from .worker import configuration


def required(name):
    value = os.environ.get('RAW_ARCHIVE_' + name, '')
    if not value or value.startswith('FILL_'):
        raise ArchiveBlocked('Missing archive configuration: ' + name)
    return value


def limits_from_environment():
    limits = Limits(
        seconds=int(os.environ.get('RAW_ARCHIVE_MAX_SECONDS', '3600')),
        bytes=int(os.environ.get('RAW_ARCHIVE_MAX_BYTES', str(1024**3))),
        references=int(os.environ.get('RAW_ARCHIVE_MAX_REFERENCES', '10000')),
        page_size=int(os.environ.get('RAW_ARCHIVE_PAGE_SIZE', '100')),
        settle_seconds=int(os.environ.get('RAW_ARCHIVE_SETTLE_SECONDS', '600')),
        recheck_days=int(os.environ.get('RAW_ARCHIVE_RECHECK_DAYS', '7')),
    )
    limits.guard()
    return limits


def source_settings(kind, namespace):
    from sqlalchemy.engine import make_url

    prefix = kind.upper()
    database = required(prefix + '_DATABASE_URL')
    url = make_url(database)
    if url.get_backend_name() != 'postgresql':
        raise ArchiveBlocked('Dedicated read-only PostgreSQL access is required')
    endpoint = required(prefix + '_S3_ENDPOINT')
    parsed = urlparse(endpoint)
    if (parsed.scheme != 'https' and not (
            parsed.scheme == 'http' and parsed.hostname in {'127.0.0.1', 'localhost', '::1'})):
        raise ArchiveBlocked('S3 requires HTTPS or explicit loopback transport')
    if parsed.username or parsed.password or parsed.query or parsed.fragment or not parsed.hostname:
        raise ArchiveBlocked('Invalid S3 endpoint')
    bucket = 'greenmind-raw' if kind == 'gateway' else required('DIRECT_S3_BUCKET')
    if kind == 'direct' and not bucket.startswith(f'greenmind-direct-{namespace}'):
        raise ArchiveBlocked('Direct source bucket must match the selected environment')
    # Exclude credentials from the journal; include DB identity to reject wrong source reuse.
    identity = hashlib.sha256(json.dumps([
        url.host, url.port, url.database, endpoint, bucket,
    ]).encode()).hexdigest()
    return dict(database=database, endpoint=endpoint, bucket=bucket, identity=identity,
                access=required(prefix + '_S3_ACCESS_KEY'),
                secret=required(prefix + '_S3_SECRET_KEY'))


def health_probe(*, allow_low_source_space=False, metrics=None):
    """Read-only local health requests plus real host memory/load/free-space checks."""
    import httpx

    urls = required('HEALTH_URLS').split(',')
    if len(urls) < 2 or len(urls) > 8:
        raise ArchiveBlocked('Configure receiver health endpoints for both pipelines')
    for url in urls:
        parsed = urlparse(url)
        if (parsed.scheme not in {'http', 'https'} or parsed.hostname not in {
                '127.0.0.1', 'localhost', '::1'} or parsed.username or parsed.password):
            raise ArchiveBlocked('Health checks must target explicit local services')
    mounts = [Path(value) for value in required('SOURCE_MOUNTS').split(',')]
    if any(not mount.is_absolute() or not mount.is_dir() for mount in mounts):
        raise ArchiveBlocked('All source storage mounts must exist')
    minimum_memory = int(os.environ.get('RAW_ARCHIVE_MIN_AVAILABLE_MEMORY_MIB', '1024'))
    maximum_load = float(os.environ.get('RAW_ARCHIVE_MAX_HOST_LOAD', '2'))
    if minimum_memory < 128 or not 0 < maximum_load <= 4:
        raise ArchiveBlocked('Unsafe host resource limits')

    return HealthProbe(urls, mounts, minimum_memory, maximum_load,
                       allow_low_source_space=allow_low_source_space, metrics=metrics)


def require_scan_index(engine, kind):
    """Fail before scanning if the separately approved, concurrent index is missing."""
    from sqlalchemy import text
    index, table = ('raw_backup_gateway_arrival', 'wav_file') if kind == 'gateway' else (
        'raw_backup_direct_arrival', 'direct_revision')
    with engine.connect() as connection:
        valid = connection.execute(text('''SELECT indisvalid AND indisready FROM pg_index
            WHERE indexrelid=to_regclass(:index) AND indrelid=to_regclass(:table)'''),
            {'index': index, 'table': table}).scalar()
        if not valid:
            raise ArchiveBlocked('Missing valid read index: ' + index)
        if connection.execute(text('SHOW transaction_read_only')).scalar() != 'on':
            raise ArchiveBlocked('Archive database connection is not read-only')


def destination_from_environment():
    from .storage import StorageBox
    return StorageBox(
        host=required('SFTP_HOST'), user=required('SFTP_USER'),
        key=Path(required('SFTP_KEY')), known_hosts=Path(required('SFTP_KNOWN_HOSTS')),
        port=int(os.environ.get('RAW_ARCHIVE_SFTP_PORT', '23')),
        root=os.environ.get('RAW_ARCHIVE_SFTP_ROOT', 'greenmind-raw'),
        kilobits=int(os.environ.get('RAW_ARCHIVE_SFTP_KILOBITS', '8192')),
    )


def run(metrics=None):
    metrics = metrics or Metrics()
    with ExitStack() as cleanup:
        return _run(metrics, cleanup)


def _run(metrics, cleanup):
    config = configuration()
    # Before keys, settings, engines, state files or health/network requests.
    if not config.enabled:
        return {'status': 'disabled', 'transfers': 0, 'deletions': 0}
    config.guard()
    if config.delete_enabled:
        config.guard(deleting=True)
    limits = limits_from_environment()
    settings = {kind: source_settings(kind, config.namespace) for kind in ('gateway', 'direct')}
    healthy = health_probe(allow_low_source_space=config.delete_enabled, metrics=metrics)
    cleanup.callback(healthy.close)
    if (config.root / 'PAUSE').exists():
        healthy.close()
        raise SafetyPause('manual_pause')
    if not healthy():
        state = healthy.last
        healthy.close()
        raise SafetyPause(state['reason'], state)

    import boto3
    from botocore.config import Config as BotoConfig
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from .catalog import DirectCatalog, GatewayCatalog
    from .storage import S3Source

    destination = destination_from_environment()
    cleanup.enter_context(destination.session(config.root,
        enabled=os.environ.get('RAW_ARCHIVE_REUSE_SSH', 'false').lower() == 'true',
        metrics=metrics))
    catalogs, sources, eviction_catalogs = {}, {}, {}
    for kind, values in settings.items():
        engine = create_engine(values['database'], pool_size=1, max_overflow=0,
                               pool_timeout=3, connect_args={
            'connect_timeout': 5,
            'options': '-c default_transaction_read_only=on -c statement_timeout=3000 '
                       '-c lock_timeout=500 -c idle_in_transaction_session_timeout=5000',
        })
        cleanup.callback(engine.dispose)
        require_scan_index(engine, kind)
        sessions = sessionmaker(bind=engine)
        catalogs[kind] = (GatewayCatalog(sessions, config.max_file_bytes, copy_only=True)
                          if kind == 'gateway' else DirectCatalog(
                              sessions, values['bucket'], copy_only=True))
        eviction_catalogs[kind] = (GatewayCatalog(sessions, config.max_file_bytes)
                                  if kind == 'gateway' else DirectCatalog(
                                      sessions, values['bucket']))
        client = boto3.client('s3', endpoint_url=values['endpoint'],
                              aws_access_key_id=values['access'],
                              aws_secret_access_key=values['secret'],
                              config=BotoConfig(connect_timeout=5, read_timeout=15,
                                                retries={'total_max_attempts': 2},
                                                s3={'addressing_style': 'path'}))
        cleanup.callback(client.close)
        sources[kind] = S3Source(client)
    return run_daily(config, catalogs, sources, destination, healthy=healthy, limits=limits,
                     source_identities={k: v['identity'] for k, v in settings.items()},
                     eviction_catalogs=eviction_catalogs, metrics=metrics)


def main():
    metrics = Metrics()
    try:
        report = run(metrics)
    except Exception as error:
        # Never print raw SQL/S3 exceptions: these can contain authentication material.
        report = {'status': 'blocked', 'error': type(error).__name__}
        if isinstance(error, ArchiveBlocked):
            report['reason'] = str(error)
        if isinstance(error, SafetyPause):
            report['pause_code'], report['guard'] = error.code, error.details
    report['telemetry'] = metrics.snapshot()
    print(json.dumps(report, sort_keys=True))
    return 0 if report['status'] in {'disabled', 'complete'} else 1
