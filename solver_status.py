"""Truthful, shared descriptions for persisted solver outcomes."""

SOLVER_STATUS = {
    "OPTIMAL": {
        "tone": "success",
        "label": "Optimal",
        "description": "The bounded solver proved the best objective value for this model.",
    },
    "FEASIBLE": {
        "tone": "success",
        "label": "Feasible",
        "description": "All hard constraints passed; a better soft-constraint score may still exist.",
    },
    "FEASIBLE_WITH_RELAXATION": {
        "tone": "warning",
        "label": "Feasible with relaxation",
        "description": "Hard constraints passed, but one or more configured soft constraints were relaxed.",
    },
    "INFEASIBLE": {
        "tone": "danger",
        "label": "Infeasible",
        "description": "The solver proved that the configured hard constraints cannot all be satisfied.",
    },
    "TIME_LIMIT_REACHED": {
        "tone": "warning",
        "label": "Time limit reached",
        "description": "The bounded search stopped at its time limit; this is not proof of infeasibility.",
    },
    "UNKNOWN": {
        "tone": "neutral",
        "label": "Unknown",
        "description": "The solver did not establish a conclusive result.",
    },
}


def solver_status_info(status: str | None) -> dict[str, str]:
    return SOLVER_STATUS.get(status or "UNKNOWN", SOLVER_STATUS["UNKNOWN"])
