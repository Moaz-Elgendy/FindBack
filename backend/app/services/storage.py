import hashlib
import json
import os
from typing import Any


def store_raw_snapshot(item_id: str, payload: dict[str, Any]) -> str | None:
    """Persist fetched content when S3/R2 credentials are configured."""
    bucket = os.getenv("S3_BUCKET")
    endpoint = os.getenv("S3_ENDPOINT")
    access_key = os.getenv("S3_ACCESS_KEY")
    secret_key = os.getenv("S3_SECRET_KEY")
    if not all((bucket, access_key, secret_key)):
        return None

    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    digest = hashlib.sha256(body).hexdigest()[:16]
    key = f"raw/{item_id}/{digest}.json"
    import boto3

    client = boto3.client(
        "s3",
        endpoint_url=endpoint or None,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name=os.getenv("S3_REGION", "auto"),
    )
    client.put_object(Bucket=bucket, Key=key, Body=body, ContentType="application/json")
    return key
