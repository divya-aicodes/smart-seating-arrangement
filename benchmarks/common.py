"""Shared benchmark result and claim-verification helpers."""

from __future__ import annotations

import json
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1
QUALIFYING_IMPORT_TARGETS = (100_000, 500_000)


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def git_revision() -> str | None:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


def runtime_metadata() -> dict[str, Any]:
    return {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "processor": platform.processor() or None,
        "git_revision": git_revision(),
    }


def write_result(result: dict[str, Any], output: str | Path) -> Path:
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def qualifying_import_claims(results: list[dict[str, Any]]) -> dict[str, bool]:
    """Qualify only complete PostgreSQL commit measurements at exact target sizes."""
    claims: dict[str, bool] = {}
    for target in QUALIFYING_IMPORT_TARGETS:
        claims[f"postgresql_import_{target}"] = any(
            result.get("suite") == "import"
            and result.get("database", {}).get("dialect") == "postgresql"
            and result.get("requested_rows") == target
            and result.get("staging", {}).get("status") == "validated"
            and result.get("staging", {}).get("valid_rows") == target
            and result.get("staging", {}).get("invalid_rows") == 0
            and result.get("commit", {}).get("status") == "completed"
            and result.get("commit", {}).get("committed_rows") == target
            for result in results
        )
    return claims


def result_envelope(suite: str) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "suite": suite,
        "measured_at": utc_timestamp(),
        "runtime": runtime_metadata(),
    }
