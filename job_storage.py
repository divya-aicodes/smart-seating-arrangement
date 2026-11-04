"""Private artifact storage for queued uploads and generated reports."""

from __future__ import annotations

import hashlib
import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from flask import current_app


class JobStorageError(RuntimeError):
    pass


def _safe_key(key: str) -> str:
    normalized = key.replace("\\", "/").strip("/")
    if not normalized or any(part in {"", ".", ".."} for part in normalized.split("/")):
        raise JobStorageError("Invalid private object key.")
    return normalized


def _local_path(key: str) -> Path:
    root_value = Path(str(current_app.config["JOB_STORAGE_DIR"]))
    root = root_value if root_value.is_absolute() else Path(current_app.root_path) / root_value
    root = root.resolve()
    target = (root / _safe_key(key)).resolve()
    if root != target and root not in target.parents:
        raise JobStorageError("Private object key escapes the storage root.")
    return target


def _s3_client():
    try:
        import boto3
    except ImportError as exc:
        raise JobStorageError("boto3 is required for S3 job storage.") from exc
    return boto3.client(
        "s3",
        endpoint_url=current_app.config.get("S3_ENDPOINT_URL"),
        region_name=current_app.config.get("S3_REGION"),
    )


def _s3_key(key: str) -> str:
    prefix = str(current_app.config.get("S3_PREFIX") or "").strip("/")
    return f"{prefix}/{_safe_key(key)}" if prefix else _safe_key(key)


def put_stream(key: str, stream) -> tuple[int, str]:
    """Store a stream privately and return byte count plus SHA-256 checksum."""
    hasher = hashlib.sha256()
    total = 0
    with tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024) as staged:
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            hasher.update(chunk)
            staged.write(chunk)
        staged.seek(0)
        if current_app.config["JOB_STORAGE_BACKEND"] == "s3":
            _s3_client().upload_fileobj(
                staged,
                current_app.config["S3_BUCKET"],
                _s3_key(key),
                ExtraArgs={"ServerSideEncryption": "AES256"},
            )
        else:
            target = _local_path(key)
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
            try:
                with temporary.open("wb") as output:
                    shutil.copyfileobj(staged, output, length=1024 * 1024)
                temporary.replace(target)
            finally:
                if temporary.exists():
                    temporary.unlink()
    return total, hasher.hexdigest()


def put_bytes(key: str, data: bytes) -> None:
    if current_app.config["JOB_STORAGE_BACKEND"] == "s3":
        _s3_client().put_object(
            Bucket=current_app.config["S3_BUCKET"],
            Key=_s3_key(key),
            Body=data,
            ServerSideEncryption="AES256",
        )
        return
    target = _local_path(key)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_bytes(data)
        temporary.replace(target)
    finally:
        if temporary.exists():
            temporary.unlink()


def get_bytes(key: str) -> bytes:
    if current_app.config["JOB_STORAGE_BACKEND"] == "s3":
        response = _s3_client().get_object(
            Bucket=current_app.config["S3_BUCKET"], Key=_s3_key(key)
        )
        return response["Body"].read()
    target = _local_path(key)
    if not target.is_file():
        raise JobStorageError("The private job artifact no longer exists.")
    return target.read_bytes()


@contextmanager
def open_seekable(key: str):
    """Yield a seekable binary stream, spilling S3 objects to disk when large."""
    if current_app.config["JOB_STORAGE_BACKEND"] == "s3":
        response = _s3_client().get_object(
            Bucket=current_app.config["S3_BUCKET"], Key=_s3_key(key)
        )
        with tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024) as staged:
            shutil.copyfileobj(response["Body"], staged, length=1024 * 1024)
            staged.seek(0)
            yield staged
        return
    target = _local_path(key)
    if not target.is_file():
        raise JobStorageError("The private job artifact no longer exists.")
    with target.open("rb") as stream:
        yield stream


def delete(key: str) -> None:
    if current_app.config["JOB_STORAGE_BACKEND"] == "s3":
        _s3_client().delete_object(
            Bucket=current_app.config["S3_BUCKET"], Key=_s3_key(key)
        )
        return
    target = _local_path(key)
    if target.exists():
        target.unlink()


def presigned_download(key: str, filename: str, content_type: str) -> str | None:
    if current_app.config["JOB_STORAGE_BACKEND"] != "s3":
        return None
    return _s3_client().generate_presigned_url(
        "get_object",
        Params={
            "Bucket": current_app.config["S3_BUCKET"],
            "Key": _s3_key(key),
            "ResponseContentType": content_type,
            "ResponseContentDisposition": f'attachment; filename="{filename}"',
        },
        ExpiresIn=300,
    )
