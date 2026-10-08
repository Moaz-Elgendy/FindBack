import hashlib
import json
import os
from typing import Any


def delete_raw_snapshots(item_ids: list[str]) -> None:
    """Remove every retained processing version belonging to these saves."""
    if not item_ids or not os.getenv('S3_BUCKET'):
        return
    from fastapi import HTTPException
    if not os.getenv('S3_ACCESS_KEY') or not os.getenv('S3_SECRET_KEY'):
        raise HTTPException(503, 'Save storage deletion is not configured')
    import boto3
    from botocore.exceptions import BotoCoreError, ClientError
    client = boto3.client('s3', endpoint_url=os.getenv('S3_ENDPOINT') or None,
        aws_access_key_id=os.getenv('S3_ACCESS_KEY'), aws_secret_access_key=os.getenv('S3_SECRET_KEY'),
        region_name=os.getenv('S3_REGION', 'auto'))
    try:
        for item_id in item_ids:
            for page in client.get_paginator('list_objects_v2').paginate(
                    Bucket=os.getenv('S3_BUCKET'), Prefix=f'raw/{item_id}/'):
                objects = [{'Key': row['Key']} for row in page.get('Contents', [])]
                if objects:
                    result = client.delete_objects(Bucket=os.getenv('S3_BUCKET'), Delete={'Objects': objects})
                    if result.get('Errors'):
                        raise HTTPException(502, 'Could not remove saved content. Please try again')
    except (BotoCoreError, ClientError):
        raise HTTPException(502, 'Could not remove saved content. Please try again') from None


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
    from sqlalchemy import text
    from app.database import SessionLocal
    # Account deletion takes this same owner lock before removing raw prefixes.
    with SessionLocal() as db, db.begin():
        owner = db.execute(text('''SELECT u.id FROM users u JOIN items i ON i.user_id=u.id
            WHERE i.id=:id FOR UPDATE OF u'''), {'id': item_id}).first()
        if owner is None:
            return None
        client.put_object(Bucket=bucket, Key=key, Body=body, ContentType="application/json")
    return key
