"""Bounded CP-SAT seating optimization with explicit, truthful outcomes."""

from dataclasses import dataclass
from typing import Any


MAX_CP_SAT_STUDENTS = 140
MAX_CP_SAT_SEATS = 220
MAX_CP_SAT_VARIABLES = 25_000
DEFAULT_TIME_LIMIT_SECONDS = 5.0

OPTIMAL = "OPTIMAL"
FEASIBLE = "FEASIBLE"
FEASIBLE_WITH_RELAXATION = "FEASIBLE_WITH_RELAXATION"
INFEASIBLE = "INFEASIBLE"
TIME_LIMIT_REACHED = "TIME_LIMIT_REACHED"
UNKNOWN = "UNKNOWN"
SKIPPED_BOUND = "SKIPPED_BOUND"
UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True)
class OptimizationResult:
    status: str
    seat_assignments: dict[str, dict[str, Any]]
    objective_value: int | None
    best_objective_bound: float | None
    wall_time_seconds: float
    message: str
    used_cp_sat: bool


def within_cp_sat_bounds(student_count, seat_count):
    return (
        student_count <= MAX_CP_SAT_STUDENTS
        and seat_count <= MAX_CP_SAT_SEATS
        and student_count * seat_count <= MAX_CP_SAT_VARIABLES
    )


def classify_unsolved_status(*, proved_infeasible, wall_time_seconds, time_limit_seconds):
    """Keep a proof of infeasibility distinct from a bounded search ending."""
    if proved_infeasible:
        return INFEASIBLE
    effective_limit = max(0.05, float(time_limit_seconds))
    if wall_time_seconds >= effective_limit * 0.95:
        return TIME_LIMIT_REACHED
    return UNKNOWN


def optimize_with_cp_sat(
    students,
    G_seats,
    *,
    fixed_assignments=None,
    initial_assignments=None,
    time_limit_seconds=DEFAULT_TIME_LIMIT_SECONDS,
):
    """Solve a bounded assignment model; never builds a student conflict graph."""
    fixed_assignments = fixed_assignments or {}
    initial_assignments = initial_assignments or {}
    seats = list(G_seats.nodes())
    if not within_cp_sat_bounds(len(students), len(seats)):
        return OptimizationResult(
            SKIPPED_BOUND, {}, None, None, 0.0,
            "CP-SAT was skipped because this run exceeds the configured model bounds.", False,
        )

    try:
        from ortools.sat.python import cp_model
    except ImportError:
        return OptimizationResult(
            UNAVAILABLE, {}, None, None, 0.0,
            "OR-Tools is not installed; the validated greedy result remains available.", False,
        )

    model = cp_model.CpModel()
    student_ids = [student["id"] for student in students]
    student_by_id = {student["id"]: student for student in students}
    seat_index = {seat_id: index for index, seat_id in enumerate(seats)}
    x = {
        (student_id, seat_id): model.new_bool_var(f"x_{student_index}_{seat_index[seat_id]}")
        for student_index, student_id in enumerate(student_ids)
        for seat_id in seats
    }

    for student_id in student_ids:
        model.add_exactly_one(x[student_id, seat_id] for seat_id in seats)
    for seat_id in seats:
        model.add_at_most_one(x[student_id, seat_id] for student_id in student_ids)

    for student_id, seat_id in fixed_assignments.items():
        if student_id not in student_by_id or seat_id not in seat_index:
            return OptimizationResult(UNKNOWN, {}, None, None, 0.0, "A fixed assignment is invalid.", False)
        model.add(x[student_id, seat_id] == 1)

    for student in students:
        if student.get("special_needs") and student["id"] not in fixed_assignments:
            for seat_id in seats:
                if not G_seats.nodes[seat_id].get("accessible", False):
                    model.add(x[student["id"], seat_id] == 0)

    groups = {}
    subjects = {}
    for student in students:
        groups.setdefault(student.get("class", "DefaultClass"), []).append(student["id"])
        subjects.setdefault(student.get("subject", "General"), []).append(student["id"])

    class_occupancy = {}
    for class_index, members in enumerate(groups.values()):
        for seat_id in seats:
            variable = model.new_bool_var(f"class_{class_index}_{seat_index[seat_id]}")
            class_occupancy[class_index, seat_id] = variable
            model.add(variable == sum(x[student_id, seat_id] for student_id in members))
    for seat_a, seat_b in G_seats.edges():
        for class_index in range(len(groups)):
            model.add(class_occupancy[class_index, seat_a] + class_occupancy[class_index, seat_b] <= 1)

    violations = []
    for subject_index, members in enumerate(subjects.values()):
        occupancy = {}
        for seat_id in seats:
            variable = model.new_bool_var(f"subject_{subject_index}_{seat_index[seat_id]}")
            occupancy[seat_id] = variable
            model.add(variable == sum(x[student_id, seat_id] for student_id in members))
        for edge_index, (seat_a, seat_b) in enumerate(G_seats.edges()):
            violation = model.new_bool_var(f"subject_edge_{subject_index}_{edge_index}")
            model.add(violation >= occupancy[seat_a] + occupancy[seat_b] - 1)
            violations.append(violation)
    model.minimize(sum(violations))

    for seat_id, student in initial_assignments.items():
        student_id = student["id"]
        if student_id in student_by_id and seat_id in seat_index:
            model.add_hint(x[student_id, seat_id], 1)

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = max(0.05, float(time_limit_seconds))
    solver.parameters.num_search_workers = 1
    raw_status = solver.solve(model)
    wall_time = solver.wall_time

    if raw_status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        assignments = {}
        for student in students:
            for seat_id in seats:
                if solver.value(x[student["id"], seat_id]):
                    student_copy = dict(student)
                    student_copy["assigned_seat"] = seat_id
                    student_copy["seat_label"] = G_seats.nodes[seat_id].get("seat_label", seat_id)
                    student_copy["room_id"] = G_seats.nodes[seat_id].get("room_id", 1)
                    assignments[seat_id] = student_copy
                    break
        objective = int(round(solver.objective_value))
        status = OPTIMAL if raw_status == cp_model.OPTIMAL else (
            FEASIBLE_WITH_RELAXATION if objective else FEASIBLE
        )
        return OptimizationResult(
            status, assignments, objective, solver.best_objective_bound, wall_time,
            "CP-SAT returned a bounded model solution.", True,
        )
    if raw_status == cp_model.INFEASIBLE:
        status = classify_unsolved_status(
            proved_infeasible=True,
            wall_time_seconds=wall_time,
            time_limit_seconds=time_limit_seconds,
        )
        return OptimizationResult(status, {}, None, None, wall_time, "The bounded CP-SAT model proved infeasible.", True)

    status = classify_unsolved_status(
        proved_infeasible=False,
        wall_time_seconds=wall_time,
        time_limit_seconds=time_limit_seconds,
    )
    timed_out = status == TIME_LIMIT_REACHED
    return OptimizationResult(
        status, {}, None, None, wall_time,
        "CP-SAT reached its time bound without a solution." if timed_out else "CP-SAT returned an unknown outcome.",
        True,
    )
