from functools import lru_cache
from typing import BinaryIO, Iterable

import boto3
from botocore.exceptions import ClientError

from app.macmini.config import get_settings

settings = get_settings()


@lru_cache(maxsize=1)
def get_s3_client():
    endpoint = settings.s3_endpoint or None
    if settings.s3_provider == "aws":
        endpoint = None

    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        region_name=settings.s3_region,
        aws_access_key_id=settings.s3_access_key_id,
        aws_secret_access_key=settings.s3_secret_access_key,
    )


def ensure_bucket_exists() -> None:
    client = get_s3_client()
    bucket = settings.s3_bucket

    try:
        client.head_bucket(Bucket=bucket)
        return
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code not in {"404", "NoSuchBucket", "NotFound"}:
            raise

    create_args = {"Bucket": bucket}
    if settings.s3_provider == "aws" and settings.s3_region != "us-east-1":
        create_args["CreateBucketConfiguration"] = {
            "LocationConstraint": settings.s3_region
        }

    client.create_bucket(**create_args)


def upload_bytes(key: str, payload: bytes, content_type: str) -> None:
    client = get_s3_client()
    client.put_object(
        Bucket=settings.s3_bucket,
        Key=key,
        Body=payload,
        ContentType=content_type,
    )


def open_object_stream(key: str):
    client = get_s3_client()
    return client.get_object(Bucket=settings.s3_bucket, Key=key)
