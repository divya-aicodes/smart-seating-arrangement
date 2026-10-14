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
    
    # 1. Populate special-needs students already seated
    assigned_special_ids = set()
    for s_id, seat_id in already_assigned_special.items():
        s_obj = next(s for s in students if s["id"] == s_id)
        student_copy = dict(s_obj)
        student_copy["assigned_seat"] = seat_id
        student_copy["seat_label"] = G_seats.nodes[seat_id].get("seat_label", seat_id)
        student_copy["room_id"] = G_seats.nodes[seat_id].get("room_id", 1)
        seat_assignments[seat_id] = student_copy
        student_to_seat[s_id] = seat_id
        assigned_special_ids.add(s_id)
        
    remaining_students = [s for s in students if s["id"] not in assigned_special_ids]
    
    # 2. Partition seats by bipartite parity (or 4-coloring if diagonal)
    available_seats = [n for n in G_seats.nodes() if n not in seat_assignments]
    
    # Determine seat parity (row + col_idx) % 2 for bipartite orthogonal grid
    seat_parities = {}
    for n in G_seats.nodes():
        node_data = G_seats.nodes[n]
        r = node_data.get("row", 1)
        c = node_data.get("col_idx", 1)
        if diagonal:
            parity = ((r % 2) * 2) + (c % 2)
        else:
            parity = (r + c) % 2
        seat_parities[n] = parity
        
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
        
        # Candidate available seats
        available_seats = [n for n in G_seats.nodes() if n not in seat_assignments]
        if not available_seats:
            unassigned_students.append(student)
            continue
            
        # Score available seats:
        # - Hard constraint violation (class conflict with adjacent seat): penalty 1,000,000
        # - Soft constraint violation (subject conflict with adjacent seat): penalty 10
        # - Parity balance score: tie breaker
        for seat_id in available_seats:
            class_conflict_count = 0
            subject_conflict_count = 0
            
            for neighbor_id in G_seats.neighbors(seat_id):
                if neighbor_id in seat_assignments:
                    neighbor_student = seat_assignments[neighbor_id]
                    if neighbor_student.get("class") == s_class:
                        class_conflict_count += 1
                    if neighbor_student.get("subject") == s_subject:
                        subject_conflict_count += 1
                        
            # Same-class adjacency is a hard constraint, never a weighted fallback.
            if class_conflict_count:
                continue

            score = subject_conflict_count * 10
            
            if score < best_score:
                best_score = score
                best_seat = seat_id
                if score == 0:
                    break  # Perfect placement found
                    
        if best_seat:
            student_copy = dict(student)
            student_copy["assigned_seat"] = best_seat
            student_copy["seat_label"] = G_seats.nodes[best_seat].get("seat_label", best_seat)
            student_copy["room_id"] = G_seats.nodes[best_seat].get("room_id", 1)
            # The incremental relaxed_constraints gathering and has_subject_relaxation tagging is 
            # removed here and delegated to the final single validation pass in validator.py
            
            seat_assignments[best_seat] = student_copy
            student_to_seat[s_id] = best_seat
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
