"""S3/MinIO client factory."""
from functools import lru_cache

import boto3
from botocore.config import Config

from app.macmini.config import get_settings

settings = get_settings()


@lru_cache(maxsize=1)
def get_s3_client():
    kwargs = {
        "service_name": "s3",
        "aws_access_key_id": settings.s3_access_key_id,
        "aws_secret_access_key": settings.s3_secret_access_key,
        "region_name": settings.s3_region,
        "config": Config(signature_version="s3v4"),
    }
    if settings.s3_provider == "minio":
        kwargs["endpoint_url"] = settings.s3_endpoint
        kwargs["config"] = Config(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
        )
    return boto3.client(**kwargs)
