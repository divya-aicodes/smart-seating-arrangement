r"""
Implements: Rook polynomial / forbidden positions technique for special-needs seat restriction.

Discrete Mathematics Core:
- Combinatorics of Forbidden Positions: Given a set of objects X = {x_1, ..., x_m} to be
  placed into positions Y = {y_1, ..., y_n}, a subset of positions F_i subset Y is forbidden
  for object x_i. The forbidden board is B = union ({x_i} x F_i).
- Rook Polynomial R(x, B): Encodes the number of ways r_k(B) to place k non-attacking rooks
  on the forbidden cells: R(x, B) = sum_{k=0} r_k(B) x^k.
- Permutation on Complementary Board: Valid assignments correspond to placing m non-attacking
  rooks strictly on the complement board B' = (X x Y) \ B, ensuring zero rooks fall on
  forbidden cells: r_m(B') with hard adjacency restrictions.
"""

def assign_special_needs_students(special_students, G_seats):
    """
    Assigns special-needs students exclusively to designated accessible seats using the
    forbidden-positions framework.
    
    Ensures:
    1. Every special-needs student is placed in an accessible seat (forbidden cells avoided).
    2. No two special-needs students of the same class are placed in adjacent seats.
    
    Returns:
      assignments: dict mapping student_id -> seat_id
      assigned_seat_ids: set of seat IDs occupied by special-needs students
      assigned_student_ids: set of student IDs placed
      math_report: dictionary detailing the forbidden-positions board and combinatorial metrics
    """
    total_seats = G_seats.number_of_nodes()
    accessible_seats = [n for n, d in G_seats.nodes(data=True) if d.get("accessible", False)]
    non_accessible_seats = [n for n, d in G_seats.nodes(data=True) if not d.get("accessible", False)]
    
    m = len(special_students)
    n = total_seats
    num_accessible = len(accessible_seats)
    num_non_accessible = len(non_accessible_seats)
    
    # Combinatorial board dimensions
    total_board_cells = m * n
    forbidden_cells_count = m * num_non_accessible
    permitted_cells_count = m * num_accessible
    
    if m == 0:
        return {}, set(), set(), {
            "special_needs_count": 0,
            "accessible_seats_count": num_accessible,
            "forbidden_cells_count": 0,
            "permitted_cells_count": 0,
            "status": "No special-needs students to allocate"
        }
    
    if m > num_accessible:
        raise ValueError(
            f"Forbidden-positions infeasibility: {m} special-needs students cannot be "
            f"assigned to only {num_accessible} permitted accessible seats."
        )
    
    # Sort students so largest class groups are placed with care
    # Group special students by class
    assignments = {}
    used_seats = set()
    
    # Backtracking solver over the permitted accessible seats ensuring class non-adjacency
    def backtrack(idx):
        if idx == len(special_students):
            return True
        
        student = special_students[idx]
        student_id = student["id"]
        student_class = student.get("class", "DefaultClass")
        
        # Try accessible seats not yet used
        for seat_id in accessible_seats:
            if seat_id in used_seats:
                continue
            
            # Check hard constraint: is this seat adjacent to any already-assigned seat with same class?
            conflict = False
            for neighbor_id in G_seats.neighbors(seat_id):
                if neighbor_id in used_seats:
                    # Find who is seated at neighbor_id
                    for s_id, assigned_seat in assignments.items():
                        if assigned_seat == neighbor_id:
                            assigned_s = next(s for s in special_students if s["id"] == s_id)
                            if assigned_s.get("class") == student_class:
                                conflict = True
                                break
                    if conflict:
                        break
            
            if not conflict:
                assignments[student_id] = seat_id
                used_seats.add(seat_id)
                if backtrack(idx + 1):
                    return True
                used_seats.remove(seat_id)
                del assignments[student_id]
                
        return False

    success = backtrack(0)
    if not success:
        raise ValueError(
            "Accessible seats exist, but the special-needs students cannot be placed "
            "without violating the hard same-class adjacency constraint."
        )
    
    # Mathematical explanation of the forbidden-positions formulation
    math_report = {
        "special_needs_count": m,
        "total_seats": n,
        "accessible_seats_count": num_accessible,
        "non_accessible_seats_count": num_non_accessible,
        "total_board_dimension": f"{m} x {n} ({total_board_cells} positions)",
        "forbidden_cells_count": forbidden_cells_count,
        "permitted_cells_count": permitted_cells_count,
        "forbidden_density": round(forbidden_cells_count / total_board_cells, 4) if total_board_cells > 0 else 0,
        "success": success,
        "assignments": [
            {
                "student_id": s_id,
                "seat_id": s_seat,
                "seat_label": G_seats.nodes[s_seat].get("seat_label", s_seat),
                "student": next(s for s in special_students if s["id"] == s_id)
            }
            for s_id, s_seat in assignments.items()
        ]
    }
    
    return assignments, used_seats, set(assignments.keys()), math_report
