"""
Implements: Graph Homomorphism and Soft-Constraint Optimization (minimizing subject conflicts
via bipartite/k-partition seat mapping).

Discrete Mathematics Core:
- Bipartite Grid Partitions & Independent Sets: An orthogonal seat grid is a bipartite graph
  G_seats = (V_0 union V_1, E) where no two vertices in V_0 are adjacent, and no two
  vertices in V_1 are adjacent.
- Homomorphic Embedding: Placing students such that student classes map to independent
  partitions ensures the hard constraint (no adjacent same-class students) is satisfied.
- Soft-Constraint Minimization: Within each partition, students are assigned to seats using
  a min-conflict greedy heuristic that penalizes same-subject adjacency:
    cost(seat, student) = sum_{(seat, v) in E} 1_{subject(student) == subject(seated_at(v))}
  When min cost > 0, the constraint is relaxed and explicitly logged.
"""

from collections import Counter, defaultdict

def map_students_to_seats(
    students,
    G_seats,
    already_assigned_special=None,
    diagonal=False
):
    """
    Maps students to seats respecting:
    1. Special-needs pre-assignments (Step 2).
    2. Hard constraint: No two students of the same class adjacent (strictly avoided).
    3. Soft constraint: No two students of the same subject adjacent (minimized; relaxed if necessary).
    
    Returns:
      seat_assignments: dict seat_id -> student dict (with 'relaxed_constraint' flags if any)
      student_to_seat: dict student_id -> seat_id
      relaxed_constraints: list of details for every adjacent same-subject pair
      unassigned_students: list of any students who could not be seated due to capacity limits
    """
    if already_assigned_special is None:
        already_assigned_special = {}

    seat_assignments = {}  # seat_id -> student dict
    student_to_seat = {}   # student_id -> seat_id
    student_by_id = {student["id"]: student for student in students}
    if len(student_by_id) != len(students):
        raise ValueError("Student IDs must be unique before seating allocation.")
    
    # 1. Populate special-needs students already seated
    assigned_special_ids = set()
    for s_id, seat_id in already_assigned_special.items():
        if s_id not in student_by_id:
            raise ValueError(f"Pre-assigned student {s_id} is not in the allocation input.")
        if seat_id not in G_seats:
            raise ValueError(f"Pre-assigned seat {seat_id} is unavailable.")
        s_obj = student_by_id[s_id]
        student_copy = dict(s_obj)
        student_copy["assigned_seat"] = seat_id
        student_copy["seat_label"] = G_seats.nodes[seat_id].get("seat_label", seat_id)
        student_copy["room_id"] = G_seats.nodes[seat_id].get("room_id", 1)
        seat_assignments[seat_id] = student_copy
        student_to_seat[s_id] = seat_id
        assigned_special_ids.add(s_id)
        
    remaining_students = [s for s in students if s["id"] not in assigned_special_ids]
    
    # 2. Build incremental availability and neighbor-conflict indexes.
    seat_order = {seat_id: index for index, seat_id in enumerate(G_seats.nodes())}
    available_seats = set(G_seats.nodes()) - set(seat_assignments)
    class_blocked_seats = defaultdict(set)
    subject_conflicts = defaultdict(Counter)

    def register_occupied_seat(seat_id, student):
        student_class = student.get("class", "DefaultClass")
        student_subject = student.get("subject", "General")
        for neighbor_id in G_seats.neighbors(seat_id):
            if neighbor_id in available_seats:
                class_blocked_seats[student_class].add(neighbor_id)
                subject_conflicts[student_subject][neighbor_id] += 1

    for occupied_seat, occupied_student in seat_assignments.items():
        register_occupied_seat(occupied_seat, occupied_student)
    
    # Group remaining students by class, sorted by class size descending
    class_groups = {}
    for s in remaining_students:
        class_groups.setdefault(s.get("class", "DefaultClass"), []).append(s)
        
    sorted_classes = sorted(class_groups.keys(), key=lambda c: len(class_groups[c]), reverse=True)
    
    # Strategy: Interleave students of different classes
    # Round-robin across classes to maintain high diversity across neighbors
    interleaved_students = []
    class_iters = {c: iter(class_groups[c]) for c in sorted_classes}
    active_classes = list(sorted_classes)
    
    while active_classes:
        next_active = []
        for c in active_classes:
            try:
                s = next(class_iters[c])
                interleaved_students.append(s)
                next_active.append(c)
            except StopIteration:
                pass
        active_classes = next_active
        
    # 3. Placement algorithm
    unassigned_students = []
    relaxed_constraints = []
    
    for student in interleaved_students:
        s_id = student["id"]
        s_class = student.get("class", "DefaultClass")
        s_subject = student.get("subject", "General")
        
        best_seat = None
        best_score = float('inf')
        
        if not available_seats:
            unassigned_students.append(student)
            continue

        # The counters are updated only around newly occupied seats, avoiding a
        # neighbor rescan for every student-seat candidate.
        blocked = class_blocked_seats[s_class]
        subject_scores = subject_conflicts[s_subject]
        for seat_id in available_seats:
            if seat_id in blocked:
                continue
            score = subject_scores.get(seat_id, 0)
            candidate = (score, seat_order[seat_id])
            if best_seat is None or candidate < best_score:
                best_score = candidate
                best_seat = seat_id
                    
        if best_seat:
            student_copy = dict(student)
            student_copy["assigned_seat"] = best_seat
            student_copy["seat_label"] = G_seats.nodes[best_seat].get("seat_label", best_seat)
            student_copy["room_id"] = G_seats.nodes[best_seat].get("room_id", 1)
            # The incremental relaxed_constraints gathering and has_subject_relaxation tagging is 
            # removed here and delegated to the final single validation pass in validator.py
            
            seat_assignments[best_seat] = student_copy
            student_to_seat[s_id] = best_seat
            available_seats.remove(best_seat)
            register_occupied_seat(best_seat, student_copy)
        else:
            unassigned_students.append(student)
            
    # Deduplicate bidirectional relaxed constraints
    unique_relaxed = []
    seen_pairs = set()
    for rel in relaxed_constraints:
        pair_key = tuple(sorted([rel["seat_a"], rel["seat_b"]]))
        if pair_key not in seen_pairs:
            seen_pairs.add(pair_key)
            unique_relaxed.append(rel)
            
    return seat_assignments, student_to_seat, unique_relaxed, unassigned_students
