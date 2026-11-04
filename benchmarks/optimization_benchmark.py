"""Measure feasibility, greedy allocation, bounded CP-SAT, and validation separately."""

from __future__ import annotations

import argparse
import math
import time
import tracemalloc
import uuid
from pathlib import Path

from benchmarks.common import result_envelope, write_result
from seating.cp_sat_optimizer import FEASIBLE, FEASIBLE_WITH_RELAXATION, OPTIMAL, optimize_with_cp_sat
from seating.feasibility_check import check_feasibility
from seating.seat_mapper import map_students_to_seats
from seating.validator import validate_seating_arrangement


DEFAULT_SIZES = (100, 500, 1_000)


def parse_sizes(value: str) -> list[int]:
    sizes = [int(item.strip()) for item in value.split(",") if item.strip()]
    if not sizes or any(size < 1 or size > 25_000 for size in sizes):
        raise argparse.ArgumentTypeError("optimization sizes must be between 1 and 25,000")
    return sizes


def synthetic_students(count: int) -> list[dict]:
    return [
        {
            "id": f"OPT-{index + 1:07d}",
            "name": f"Optimization Student {index + 1}",
            "class": f"C{(index % 8) + 1:02d}",
            "subject": f"Subject {(index % 12) + 1}",
            "special_needs": False,
        }
        for index in range(count)
    ]


def room_configuration(student_count: int) -> list[dict]:
    seats_per_row = 15 * 4
    seats_per_room = 20 * seats_per_row
    rooms = []
    remaining = student_count
    for room in range(math.ceil(student_count / seats_per_room)):
        room_students = min(remaining, seats_per_room)
        rooms.append({
            "room_id": room + 1,
            "rows": math.ceil(room_students / seats_per_row),
            "cols": 15,
            "seats_per_bench": 4,
            "accessible_seats": ["row_1"],
            "unavailable_seats": [],
        })
        remaining -= room_students
    return rooms


def hard_violation_count(certificate: dict) -> int:
    return sum(
        proposition["violation_count"]
        for proposition in certificate["propositions"]
        if "HARD" in proposition["type"]
    )


def run_one(student_count: int, time_limit: float, output_dir: Path) -> Path:
    students = synthetic_students(student_count)
    rooms = room_configuration(student_count)
    result = result_envelope("optimization")
    result.update({
        "requested_students": student_count,
        "room_count": len(rooms),
        "cp_sat_time_limit_seconds": time_limit,
        "dataset": "deterministic_synthetic_in_memory",
    })

    tracemalloc.start()
    total_started = time.perf_counter()
    started = time.perf_counter()
    feasibility, seat_graph = check_feasibility(students, rooms, diagonal=False)
    result["feasibility"] = {
        "seconds": round(time.perf_counter() - started, 6),
        "is_feasible": feasibility["is_feasible"],
        "hard_violation_guaranteed": feasibility["hard_violation_guaranteed"],
        "diagnostic_count": len(feasibility["diagnostics"]),
    }
    result["graph"] = {"vertices": seat_graph.number_of_nodes(), "edges": seat_graph.number_of_edges()}

    started = time.perf_counter()
    assignments, _student_to_seat, _relaxed, unassigned = map_students_to_seats(students, seat_graph)
    result["greedy"] = {
        "seconds": round(time.perf_counter() - started, 6),
        "assigned_students": len(assignments),
        "unassigned_students": len(unassigned),
    }

    started = time.perf_counter()
    cp_sat = optimize_with_cp_sat(
        students,
        seat_graph,
        initial_assignments=assignments if not unassigned else {},
        time_limit_seconds=time_limit,
    )
    result["cp_sat"] = {
        "attempt_seconds": round(time.perf_counter() - started, 6),
        "solver_wall_time_seconds": round(cp_sat.wall_time_seconds, 6),
        "status": cp_sat.status,
        "used": cp_sat.used_cp_sat,
        "objective_value": cp_sat.objective_value,
        "best_objective_bound": cp_sat.best_objective_bound,
        "message": cp_sat.message,
    }
    if cp_sat.seat_assignments:
        assignments = cp_sat.seat_assignments
        unassigned = []

    started = time.perf_counter()
    certificate = validate_seating_arrangement(assignments, seat_graph, students)
    validation_seconds = time.perf_counter() - started
    hard_violations = hard_violation_count(certificate) + len(unassigned)
    soft_violations = next(
        proposition["violation_count"]
        for proposition in certificate["propositions"]
        if proposition["id"] == "phi_2"
    )
    if hard_violations:
        final_status = "INVALID"
    elif cp_sat.status == OPTIMAL:
        final_status = OPTIMAL
    else:
        final_status = FEASIBLE_WITH_RELAXATION if soft_violations else FEASIBLE
    result["validation"] = {
        "seconds": round(validation_seconds, 6),
        "overall_valid": certificate["overall_valid"] and not unassigned,
        "checked_edges": certificate["checked_edges_total"],
        "hard_violations": hard_violations,
        "soft_violations": soft_violations,
    }
    result["final_status"] = final_status
    result["total_seconds"] = round(time.perf_counter() - total_started, 6)
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    result["python_peak_allocated_mib"] = round(peak / (1024 * 1024), 3)

    token = uuid.uuid4().hex[:10]
    return write_result(result, output_dir / f"optimization-{student_count}-{token}.json")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sizes", type=parse_sizes, default=list(DEFAULT_SIZES))
    parser.add_argument("--time-limit", type=float, default=5.0)
    parser.add_argument("--output-dir", type=Path, default=Path("benchmark-results"))
    args = parser.parse_args()
    if args.time_limit < 0.05 or args.time_limit > 120:
        parser.error("--time-limit must be between 0.05 and 120 seconds")
    for size in args.sizes:
        output = run_one(size, args.time_limit, args.output_dir)
        print(f"Wrote {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
