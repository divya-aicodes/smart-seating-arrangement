"""
Unit tests for the Smart Examination Seating Arrangement discrete mathematics engine.
Tests Steps 1 through 6 independently.
"""

import unittest
import networkx as nx
import sympy as sp

from seating.feasibility_check import (
    build_seat_adjacency_graph,
    compute_independence_number_bound,
    check_feasibility
)
from seating.forbidden_positions import assign_special_needs_students
from seating.graph_coloring import (
    build_student_conflict_graph,
    verify_handshaking_lemma,
    color_conflict_graph,
    compute_chromatic_polynomial_symbolic,
    get_small_worked_chromatic_polynomial
)
from seating.seat_mapper import map_students_to_seats
from seating.validator import validate_seating_arrangement
from seating.counting import nPr, nCr, compute_combinatorial_counts


class TestDiscreteMathSeatingEngine(unittest.TestCase):

    def setUp(self):
        # Standard 3x2 room with 2 seats per bench = 12 seats
        self.room_configs = [{
            "room_id": 1,
            "rows": 3,
            "cols": 2,
            "seats_per_bench": 2,
            "accessible_seats": ["R1-r1-c1-s1", "R1-r1-c1-s2", "R1-r1-c2-s1", "R1-r1-c2-s2"]
        }]
        
        # 10 balanced students across 3 classes and 2 subjects
        self.students_balanced = [
            {"id": f"STU{i:02d}", "class": f"CS-{(i % 3) + 1}", "subject": "Math" if i % 2 == 0 else "Physics", "special_needs": (i == 0)}
            for i in range(10)
        ]

    def test_step1_feasibility_check(self):
        """Step 1: Test Pigeonhole Principle pre-check and degree saturation."""
        status, G_seats = check_feasibility(self.students_balanced, self.room_configs)
        self.assertTrue(status["is_feasible"])
        self.assertEqual(status["total_seats"], 12)
        self.assertEqual(status["total_students"], 10)
        self.assertEqual(status["independence_number_alpha"], 6)
        
        # Test extreme pigeonhole violation: 8 students from same class when alpha=6
        unfeasible_students = [
            {"id": f"STU{i:02d}", "class": "CS-Overload", "subject": "Math", "special_needs": False}
            for i in range(8)
        ]
        status_unf, _ = check_feasibility(unfeasible_students, self.room_configs)
        self.assertFalse(status_unf["is_feasible"])
        self.assertTrue(status_unf["hard_violation_guaranteed"])
        self.assertTrue(any("PIGEONHOLE" in d["message"] for d in status_unf["diagnostics"]))

    def test_independence_bound_supports_disconnected_multi_room_graph(self):
        second_room = dict(self.room_configs[0])
        second_room["room_id"] = 2
        second_room["accessible_seats"] = []
        graph = build_seat_adjacency_graph([self.room_configs[0], second_room])

        self.assertFalse(nx.is_connected(graph))
        self.assertEqual(compute_independence_number_bound(graph), 12)

    def test_step2_forbidden_positions(self):
        """Step 2: Test forbidden positions assignment for special-needs students."""
        G_seats = build_seat_adjacency_graph(self.room_configs)
        special_students = [
            {"id": "SPEC01", "class": "CS-1", "subject": "Math", "special_needs": True},
            {"id": "SPEC02", "class": "CS-2", "subject": "Physics", "special_needs": True}
        ]
        assignments, used_seats, assigned_ids, report = assign_special_needs_students(special_students, G_seats)
        
        self.assertEqual(len(assignments), 2)
        for s_id, seat_id in assignments.items():
            # Check that every assigned seat is accessible
            self.assertTrue(G_seats.nodes[seat_id]["accessible"])
            
        self.assertEqual(report["special_needs_count"], 2)
        self.assertEqual(report["accessible_seats_count"], 4)
        self.assertGreater(report["forbidden_cells_count"], 0)

    def test_special_needs_hard_conflict_raises_instead_of_falling_back(self):
        room = [{
            "room_id": 1,
            "rows": 1,
            "cols": 1,
            "seats_per_bench": 2,
            "accessible_seats": ["R1-r1-c1-s1", "R1-r1-c1-s2"],
        }]
        G_seats = build_seat_adjacency_graph(room)
        students = [
            {"id": "S1", "class": "ClassA", "subject": "Math", "special_needs": True},
            {"id": "S2", "class": "ClassA", "subject": "Math", "special_needs": True},
        ]
        with self.assertRaisesRegex(ValueError, "hard same-class adjacency"):
            assign_special_needs_students(students, G_seats)

    def test_step3_handshaking_and_chromatic_polynomial(self):
        """Step 3: Test Handshaking Lemma (sum deg = 2|E|) and Chromatic Polynomial."""
        G_conflict = build_student_conflict_graph(self.students_balanced)
        handshaking = verify_handshaking_lemma(G_conflict)
        
        # Handshaking verification
        self.assertTrue(handshaking["handshaking_holds"])
        self.assertEqual(handshaking["sum_degrees"], 2 * handshaking["num_edges"])
        self.assertTrue(handshaking["even_odd_count_property"])
        
        # Graph coloring
        coloring, num_colors, partitions = color_conflict_graph(G_conflict)
        self.assertGreater(num_colors, 0)
        
        # Verify that students with same color have distinct classes
        for color_id, student_ids in partitions.items():
            classes = [G_conflict.nodes[s_id]["cls"] for s_id in student_ids]
            self.assertEqual(len(classes), len(set(classes)), "Conflict graph coloring must separate same classes")
            
        # Chromatic polynomial worked example (C_4)
        worked_example = get_small_worked_chromatic_polynomial()
        self.assertEqual(worked_example["vertices_count"], 4)
        self.assertEqual(worked_example["evaluations"][1], 0)  # 1 color cannot properly color C_4
        self.assertEqual(worked_example["evaluations"][2], 2)  # 2 colors on bipartite C_4 has 2 colorings
        self.assertEqual(worked_example["evaluations"][3], 18) # P(C_4, 3) = 18

    def test_step4_and_step5_seat_mapping_and_validation(self):
        """Step 4 & 5: Test seat mapping and explicit propositional logic validator."""
        G_seats = build_seat_adjacency_graph(self.room_configs)
        special_students = [s for s in self.students_balanced if s["special_needs"]]
        spec_assign, used_seats, _, _ = assign_special_needs_students(special_students, G_seats)
        
        seat_assign, stud_to_seat, relaxed, unassigned = map_students_to_seats(
            self.students_balanced,
            G_seats,
            already_assigned_special=spec_assign
        )
        
        self.assertEqual(len(seat_assign), 10)
        self.assertEqual(len(unassigned), 0)
        
        # Step 5: Validate propositional logic predicates
        cert = validate_seating_arrangement(seat_assign, G_seats, self.students_balanced)
        
        # Hard constraints must be strictly satisfied
        self.assertTrue(cert["overall_valid"])
        phi_1 = next(p for p in cert["propositions"] if p["id"] == "phi_1")
        self.assertTrue(phi_1["satisfied"])
        self.assertEqual(phi_1["violation_count"], 0)
        
        phi_3 = next(p for p in cert["propositions"] if p["id"] == "phi_3")
        self.assertTrue(phi_3["satisfied"])

    def test_mapper_leaves_student_unassigned_instead_of_breaking_hard_constraint(self):
        room = [{
            "room_id": 1,
            "rows": 1,
            "cols": 1,
            "seats_per_bench": 2,
            "accessible_seats": [],
        }]
        G_seats = build_seat_adjacency_graph(room)
        students = [
            {"id": "S1", "class": "ClassA", "subject": "Math", "special_needs": False},
            {"id": "S2", "class": "ClassA", "subject": "Physics", "special_needs": False},
        ]
        seat_assign, _, _, unassigned = map_students_to_seats(students, G_seats)
        self.assertEqual(len(seat_assign), 1)
        self.assertEqual([student["id"] for student in unassigned], ["S2"])

    def test_diagonal_independence_bound_is_exact_for_king_grid(self):
        students = [
            {"id": f"D{i}", "class": "Dense", "subject": "Math", "special_needs": False}
            for i in range(5)
        ]
        status, _ = check_feasibility(students, self.room_configs, diagonal=True)
        self.assertEqual(status["independence_number_alpha"], 4)
        self.assertTrue(status["hard_violation_guaranteed"])

    def test_step6_combinatorial_counting(self):
        """Step 6: Test Product Rule and Principle of Inclusion-Exclusion."""
        G_seats = build_seat_adjacency_graph(self.room_configs)
        counts = compute_combinatorial_counts(self.students_balanced, G_seats)
        
        n = len(self.students_balanced)
        S = G_seats.number_of_nodes()
        
        # Product rule P(12, 10)
        expected_permutations = nPr(12, 10)
        self.assertEqual(counts["total_arrangements"], expected_permutations)
        self.assertGreater(counts["S_1_value"], 0)
        self.assertGreaterEqual(counts["S_1_value"], counts["S_2_value"])


if __name__ == "__main__":
    unittest.main()
