"""Measure the real staged PostgreSQL CSV import and commit pipeline."""

from __future__ import annotations

import argparse
import csv
import os
import tempfile
import time
import tracemalloc
import uuid
from datetime import date, time as clock_time
from pathlib import Path

from werkzeug.datastructures import FileStorage

from benchmarks.common import result_envelope, write_result


DEFAULT_SIZES = (1_000, 10_000)
ALLOWED_SIZES = (1_000, 10_000, 50_000, 100_000, 250_000, 500_000)


def parse_sizes(value: str) -> list[int]:
    sizes = [int(item.strip()) for item in value.split(",") if item.strip()]
    if not sizes or any(size not in ALLOWED_SIZES for size in sizes):
        allowed = ", ".join(f"{size:,}" for size in ALLOWED_SIZES)
        raise argparse.ArgumentTypeError(f"sizes must be selected from: {allowed}")
    return sizes


def require_disposable_postgres() -> str:
    database_url = os.environ.get("BENCHMARK_DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError("BENCHMARK_DATABASE_URL must name a disposable PostgreSQL database.")
    if os.environ.get("BENCHMARK_ALLOW_WRITES") != "1":
        raise RuntimeError("Set BENCHMARK_ALLOW_WRITES=1 to confirm benchmark database writes.")
    if not database_url.startswith(("postgresql://", "postgresql+psycopg://", "postgres://")):
        raise RuntimeError("Import benchmarks require PostgreSQL; SQLite results do not qualify.")
    return database_url


def generate_csv(path: Path, row_count: int, run_token: str) -> float:
    started = time.perf_counter()
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow([
            "ID", "Name", "Class", "Department", "Subject_Code", "Subject", "Special_Needs"
        ])
        for index in range(row_count):
            class_number = index % 24 + 1
            subject_number = index % 12 + 1
            writer.writerow([
                f"BM-{run_token}-{index + 1:07d}",
                f"Benchmark Student {index + 1}",
                f"C{class_number:02d}",
                f"Department {(index % 6) + 1}",
                f"SUB{subject_number:02d}",
                f"Subject {subject_number}",
                "false",
            ])
    return time.perf_counter() - started


def run_one(row_count: int, chunk_size: int, output_dir: Path) -> Path:
    database_url = require_disposable_postgres()
    os.environ["DATABASE_URL"] = database_url
    os.environ.setdefault("APP_ENV", "development")

    # Import after selecting the isolated database; app configuration is module-scoped.
    from app import app
    from data_service import commit_staged_student_import, stage_student_import
    from extensions import db
    from models import Student
    from sqlalchemy import func, select

    run_token = uuid.uuid4().hex[:10]
    result = result_envelope("import")
    result.update({
        "requested_rows": row_count,
        "chunk_size": chunk_size,
        "database": {"dialect": "postgresql", "disposable_database_required": True},
    })

    with tempfile.TemporaryDirectory(prefix="seatplan-benchmark-") as temporary:
        csv_path = Path(temporary) / f"students-{row_count}.csv"
        result["csv_generation_seconds"] = round(generate_csv(csv_path, row_count, run_token), 6)
        result["csv_bytes"] = csv_path.stat().st_size

        tracemalloc.start()
        with app.app_context():
            if db.engine.dialect.name != "postgresql":
                raise RuntimeError("Configured benchmark connection did not resolve to PostgreSQL.")
            with csv_path.open("rb") as handle:
                upload = FileStorage(stream=handle, filename=csv_path.name, content_type="text/csv")
                started = time.perf_counter()
                import_record, reused = stage_student_import(
                    upload,
                    dataset_name=f"SeatPlan import benchmark {run_token}",
                    dataset_version=str(row_count),
                    exam_date=date(2035, 1, 15),
                    start_time=clock_time(9, 0),
                    end_time=clock_time(12, 0),
                    session_name="Benchmark session",
                    academic_year="2034-35",
                    chunk_size=chunk_size,
                )
                stage_seconds = time.perf_counter() - started
            result["staging"] = {
                "status": import_record.status,
                "seconds": round(stage_seconds, 6),
                "rows_per_second": round(import_record.processed_rows / stage_seconds, 2),
                "total_rows": import_record.total_rows,
                "valid_rows": import_record.valid_rows,
                "invalid_rows": import_record.invalid_rows,
                "reused": reused,
                "import_id": import_record.id,
            }

            started = time.perf_counter()
            dataset, _exam, committed_import = commit_staged_student_import(import_record.id)
            commit_seconds = time.perf_counter() - started
            committed_rows = db.session.scalar(
                select(func.count(Student.id)).where(Student.dataset_id == dataset.id)
            )
            result["commit"] = {
                "status": committed_import.status,
                "seconds": round(commit_seconds, 6),
                "rows_per_second": round(committed_rows / commit_seconds, 2),
                "committed_rows": committed_rows,
                "dataset_id": dataset.id,
            }
        _current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        result["python_peak_allocated_mib"] = round(peak / (1024 * 1024), 3)
        result["total_pipeline_seconds"] = round(
            result["staging"]["seconds"] + result["commit"]["seconds"], 6
        )

    output = output_dir / f"import-{row_count}-{run_token}.json"
    return write_result(result, output)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sizes", type=parse_sizes, default=list(DEFAULT_SIZES))
    parser.add_argument("--chunk-size", type=int, default=5_000, choices=range(100, 20_001))
    parser.add_argument("--output-dir", type=Path, default=Path("benchmark-results"))
    args = parser.parse_args()
    for size in args.sizes:
        output = run_one(size, args.chunk_size, args.output_dir)
        print(f"Wrote {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
