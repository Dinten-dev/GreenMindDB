"""Explicit existing credentials and read-only SQL for the source-side pilot."""

import os


def adapters(config, cleanup):
    import boto3
    from botocore.config import Config as BotoConfig
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from .catalog import DirectCatalog, GatewayCatalog
    from .diagnostics import client_from_environment
    from .runner import destination_from_environment, health_probe, required, source_settings
    from .storage import S3Source

    healthy = health_probe()
    cleanup.callback(healthy.close)
    destination = destination_from_environment()
    cleanup.callback(destination.close)
    diagnostic = client_from_environment()
    cleanup.callback(diagnostic.close)
    catalogs, sources = {}, {}
    for kind in ("gateway", "direct"):
        values = source_settings(kind, config.namespace)
        engine = create_engine(
            values["database"],
            pool_size=1,
            max_overflow=0,
            connect_args={
                "connect_timeout": 5,
                "options": "-c default_transaction_read_only=on -c statement_timeout=3000 "
                "-c lock_timeout=150 -c idle_in_transaction_session_timeout=5000",
            },
        )
        cleanup.callback(engine.dispose)
        sessions = sessionmaker(bind=engine)
        catalogs[kind] = (
            GatewayCatalog(sessions, config.max_file_bytes)
            if kind == "gateway"
            else DirectCatalog(sessions, values["bucket"])
        )
        client = boto3.client(
            "s3",
            endpoint_url=values["endpoint"],
            aws_access_key_id=required("DELETE_" + kind.upper() + "_S3_ACCESS_KEY")
            if config.delete_enabled
            else values["access"],
            aws_secret_access_key=required("DELETE_" + kind.upper() + "_S3_SECRET_KEY")
            if config.delete_enabled
            else values["secret"],
            config=BotoConfig(
                connect_timeout=5,
                read_timeout=15,
                retries={"total_max_attempts": 1},
                s3={"addressing_style": "path"},
            ),
        )
        cleanup.callback(client.close)
        sources[kind] = S3Source(
            client,
            diagnostic_client=diagnostic,
            allow_legacy_null=os.environ.get("RAW_ARCHIVE_LEGACY_NULL_ACCEPTED") == "true",
        )
    return sources, catalogs, destination, healthy
