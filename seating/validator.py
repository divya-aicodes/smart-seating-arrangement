"""
Implements: Propositional Logic and Predicate Calculus validation of formal seating constraints.

Discrete Mathematics Core:
- First-Order Logic Predicates and Universal Quantifiers:
  phi_1 (Hard Class Non-Adjacency):
    forall (u, v) in E_adj: (occupied(u) land occupied(v)) implies (class(u) != class(v))
  phi_2 (Soft Subject Non-Adjacency):
    forall (u, v) in E_adj: (occupied(u) land occupied(v)) implies (subject(u) != subject(v))
  phi_3 (Forbidden Positions for Special Needs):
    forall s in Students: special_needs(s) implies accessible(seat(s))
  phi_4 (Injective Seat Mapping / No Double-Booking):
    forall s_1, s_2 in Students: (s_1 != s_2) implies (seat(s_1) != seat(s_2))
"""


def validate_seating_arrangement(seat_assignments, G_seats, original_students):
    """
    Executes explicit propositional logic evaluations over all edges in the seat graph.
    Returns a formal validation certificate.
    """
    checked_edges_count = 0
    occupied_edges_count = 0
    
    class_violations = []
    subject_relaxations = []
    
    # Track student placement uniqueness (phi_4)
    seen_student_ids = {}
    double_booked_seats = []
    
    for seat_id, student in seat_assignments.items():
        s_id = student["id"]
        # Clear any intermediate flags to ensure truth is derived purely from final graph
        student["has_class_conflict"] = False
        student["has_subject_relaxation"] = False
        
        if s_id in seen_student_ids:
            double_booked_seats.append({
                "student_id": s_id,
                "first_seat": seen_student_ids[s_id],
                "second_seat": seat_id
            })
        seen_student_ids[s_id] = seat_id
        
    # Evaluate universal quantification across all undirected edges in G_seats
    for u, v in G_seats.edges():
        checked_edges_count += 1
        
        u_student = seat_assignments.get(u)
        v_student = seat_assignments.get(v)
        
        # If both seats are occupied, test propositional predicates
        if u_student and v_student:
            occupied_edges_count += 1
            
            # 1. Hard constraint phi_1: class(u) != class(v)
            if u_student.get("class") == v_student.get("class"):
                u_student["has_class_conflict"] = True
                v_student["has_class_conflict"] = True
                class_violations.append({
                    "seat_u": u,
                    "seat_v": v,
                    "seat_u_label": G_seats.nodes[u].get("seat_label", u),
                    "seat_v_label": G_seats.nodes[v].get("seat_label", v),
                    "student_u": u_student["id"],
                    "student_v": v_student["id"],
                    "conflicting_class": u_student.get("class")
                })
                
            # 2. Soft constraint phi_2: subject(u) != subject(v)
            if u_student.get("subject") == v_student.get("subject"):
                u_student["has_subject_relaxation"] = True
                v_student["has_subject_relaxation"] = True
                subject_relaxations.append({
                    "seat_u": u,
                    "seat_v": v,
                    "seat_u_label": G_seats.nodes[u].get("seat_label", u),
                    "seat_v_label": G_seats.nodes[v].get("seat_label", v),
                    "student_u": u_student["id"],
                    "student_v": v_student["id"],
                    "subject": u_student.get("subject")
                })
                
    # 3. Accessibility predicate phi_3: special_needs(s) => accessible(seat(s))
    accessibility_violations = []
    for s in original_students:
        if s.get("special_needs", False):
            s_id = s["id"]
            # Check where s is seated
            seat_id = next((seat for seat, stud in seat_assignments.items() if stud["id"] == s_id), None)
            if seat_id:
                is_accessible = G_seats.nodes[seat_id].get("accessible", False)
                if not is_accessible:
                    accessibility_violations.append({
                        "student_id": s_id,
                        "seat_id": seat_id,
                        "seat_label": G_seats.nodes[seat_id].get("seat_label", seat_id)
                    })
            else:
                accessibility_violations.append({
                    "student_id": s_id,
                    "seat_id": None,
                    "reason": "Special-needs student was not assigned a seat"
                })
                
    # Truth values of the propositions
    phi_1_truth = (len(class_violations) == 0)
    phi_2_truth = (len(subject_relaxations) == 0)
    phi_3_truth = (len(accessibility_violations) == 0)
    phi_4_truth = (len(double_booked_seats) == 0)
    
    # Construct formal verification certificate
    certificate = {
        "overall_valid": phi_1_truth and phi_3_truth and phi_4_truth,
        "checked_edges_total": checked_edges_count,
        "occupied_edges_evaluated": occupied_edges_count,
        "propositions": [
            {
                "id": "phi_1",
                "symbolic_formula": "forall (u, v) in E_adj : (occupied(u) land occupied(v)) implies (class(u) != class(v))",
                "latex_formula": r"\forall (u, v) \in E_{\text{adj}} : (\text{occupied}(u) \wedge \text{occupied}(v)) \implies \text{class}(u) \neq \text{class}(v)",
                "type": "HARD CONSTRAINT",
                "satisfied": phi_1_truth,
                "truth_value": "TRUE (SAT)" if phi_1_truth else "FALSE (UNSAT)",
                "violation_count": len(class_violations),
                "violations": class_violations
            },
            {
                "id": "phi_2",
                "symbolic_formula": "forall (u, v) in E_adj : (occupied(u) land occupied(v)) implies (subject(u) != subject(v))",
                "latex_formula": r"\forall (u, v) \in E_{\text{adj}} : (\text{occupied}(u) \wedge \text{occupied}(v)) \implies \text{subject}(u) \neq \text{subject}(v)",
                "type": "SOFT CONSTRAINT (RELAXABLE)",
                "satisfied": phi_2_truth,
                "truth_value": "TRUE (0 Relaxations)" if phi_2_truth else f"RELAXED ({len(subject_relaxations)} instances)",
                "violation_count": len(subject_relaxations),
                "violations": subject_relaxations
            },
            {
                "id": "phi_3",
                "symbolic_formula": "forall s in S : special_needs(s) implies accessible(seat(s))",
                "latex_formula": r"\forall s \in S : \text{special\_needs}(s) \implies \text{accessible}(\text{seat}(s))",
                "type": "HARD CONSTRAINT",
                "satisfied": phi_3_truth,
                "truth_value": "TRUE (SAT)" if phi_3_truth else "FALSE (UNSAT)",
                "violation_count": len(accessibility_violations),
                "violations": accessibility_violations
            },
            {
                "id": "phi_4",
                "symbolic_formula": "forall s_1, s_2 in S : (s_1 != s_2) implies (seat(s_1) != seat(s_2))",
                "latex_formula": r"\forall s_1, s_2 \in S : (s_1 \neq s_2) \implies \text{seat}(s_1) \neq \text{seat}(s_2)",
                "type": "HARD CONSTRAINT",
                "satisfied": phi_4_truth,
                "truth_value": "TRUE (SAT)" if phi_4_truth else "FALSE (UNSAT)",
                "violation_count": len(double_booked_seats),
                "violations": double_booked_seats
            }
        ]
    }
    
    return certificate
