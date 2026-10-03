"""Bucket metadata only. Credentials are supplied explicitly, never discovered."""

import json
import os
from urllib.parse import urlsplit

from .policy import ArchiveBlocked


def client_from_environment():
    import boto3
    from botocore.config import Config

    values = [
        os.environ.get("RAW_ARCHIVE_DIAGNOSTIC_" + key, "")
        for key in ("ENDPOINT", "ACCESS_KEY", "SECRET_KEY")
    ]
    if any(not value or value.startswith("FILL_") for value in values):
        raise ArchiveBlocked("Explicit bucket diagnostic credentials have not been provided")
    endpoint = urlsplit(values[0])
    if (
        endpoint.username
        or endpoint.password
        or endpoint.query
        or endpoint.fragment
        or not endpoint.hostname
        or not (
            endpoint.scheme == "https"
            or endpoint.scheme == "http"
            and endpoint.hostname in {"localhost", "127.0.0.1", "::1"}
        )
    ):
        raise ArchiveBlocked("Diagnostics require HTTPS or explicit loopback")
    return boto3.client(
        "s3",
        endpoint_url=values[0],
        aws_access_key_id=values[1],
        aws_secret_access_key=values[2],
        config=Config(
            connect_timeout=3,
            read_timeout=8,
            retries={"total_max_attempts": 1},
            s3={"addressing_style": "path"},
        ),
    )


def inspect_buckets(client, buckets):
    from botocore.exceptions import ClientError

    results = []
    for bucket in buckets:
        result = {"bucket": bucket}
        for label, method in (
            ("versioning", client.get_bucket_versioning),
            ("lifecycle", client.get_bucket_lifecycle_configuration),
        ):
            try:
                data = method(Bucket=bucket)
                data.pop("ResponseMetadata", None)
                result[label] = data
            except ClientError as error:
                result[label] = {"error": error.response["Error"]["Code"]}
        results.append(result)
    return results


def main():
    client = client_from_environment()
    try:
        direct = os.environ.get("RAW_ARCHIVE_DIRECT_S3_BUCKET", "")
        if not direct.startswith("greenmind-direct-production"):
            raise ArchiveBlocked("Configure the exact production Direct bucket")
        print(
            json.dumps(
                {"read_only": True, "buckets": inspect_buckets(client, ["greenmind-raw", direct])}
            )
        )
    finally:
        client.close()


if __name__ == "__main__":
    main()
