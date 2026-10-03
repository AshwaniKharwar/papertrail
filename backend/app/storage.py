import os

import boto3
from botocore.config import Config


def endpoint() -> str:
    return os.getenv("R2_ENDPOINT_URL") or os.getenv("R2_ENDPOINT") or (f"https://{os.environ['R2_ACCOUNT_ID']}.r2.cloudflarestorage.com" if os.getenv("R2_ACCOUNT_ID") else "")


def bucket() -> str:
    return os.getenv("R2_BUCKET") or os.getenv("R2_BUCKET_NAME") or ""


def ready() -> bool:
    return bool(endpoint() and bucket() and os.getenv("R2_ACCESS_KEY_ID") and os.getenv("R2_SECRET_ACCESS_KEY"))


def client():
    if not ready():
        raise RuntimeError("R2 storage is not configured")
    return boto3.client(
        "s3",
        endpoint_url=endpoint(),
        aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
        region_name="auto",
        config=Config(signature_version="s3v4", retries={"max_attempts": 3, "mode": "standard"}),
    )


def upload(fileobj, key: str):
    client().upload_fileobj(fileobj, bucket(), key, ExtraArgs={"ContentType": "application/pdf"})


def download(fileobj, key: str):
    client().download_fileobj(bucket(), key, fileobj)


def open_object(key: str, byte_range: str | None = None):
    options = {"Bucket": bucket(), "Key": key}
    if byte_range:
        options["Range"] = byte_range
    return client().get_object(**options)


def delete(key: str):
    client().delete_object(Bucket=bucket(), Key=key)
