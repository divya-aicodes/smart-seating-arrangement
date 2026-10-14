"""
Implements: The Product Rule of Combinatorics and the Principle of Inclusion-Exclusion (PIE).

Discrete Mathematics Core:
- Product Rule: Total possible unconstrained student-to-seat assignments:
    P(S, n) = S! / (S - n)! = S * (S - 1) * ... * (S - n + 1)
- Principle of Inclusion-Exclusion (PIE):
  Let A_e be the event that edge e in E_adj has both ends assigned to students of the
  same class. The total number of violating configurations with at least one adjacent
  same-class collision is:
    N(>= 1) = |union_{e in E} A_e| = S_1 - S_2 + S_3 - ... + (-1)^{m-1} S_m
  where:
    S_1 = sum_{e in E} |A_e| = |E| * 2 * sum_c ( |C_c| choose 2 ) * P(S - 2, n - 2)
    S_2 = sum_{e < e'} |A_e intersect A_e'| (accounting for incident vs disjoint edge pairs).
  Bonferroni Inequalities provide rigorous bounds:
    S_1 - S_2 <= N(>= 1) <= S_1
"""

from collections import Counter
import math
import sympy as sp


def nPr(n, r):
    """Computes permutation count P(n, r) = n! / (n - r)!"""
    if r < 0 or r > n:
        return 0
    return math.perm(n, r)


def nCr(n, r):
    """Computes combination count C(n, r) = n! / (r! * (n - r)!)"""
    if r < 0 or r > n:
        return 0
    return math.comb(n, r)


def format_large_number(num):
    """Formats large combinatorial numbers in human-readable and scientific notation."""
    num_str = str(num)
    if len(num_str) > 15:
        return f"{float(num):.4e} ({len(num_str)} digits)"
    else:
        return f"{num:,}"


def compute_combinatorial_counts(students, G_seats):
    """
    Computes theoretical counts using the Product Rule and Principle of Inclusion-Exclusion:
    1. Total unconstrained seatings P(S, n).
    2. Exact first-order PIE term S_1.
    3. Second-order PIE term S_2 and Bonferroni interval [S_1 - S_2, S_1].
    4. Proportion of sample space that violates constraints.
    """
    n = len(students)
    S = G_seats.number_of_nodes()
    E_count = G_seats.number_of_edges()
    
    class_counts = Counter(s.get("class", "DefaultClass") for s in students)
    
    # 1. Product Rule: Total unconstrained seatings
    total_arrangements = nPr(S, n)
    
    # 2. First-order PIE calculation S_1
    # Sum over classes of choose(|C_c|, 2)
    same_class_pairs = sum(nCr(cnt, 2) for cnt in class_counts.values())
    
    if n >= 2 and S >= 2:
        # Each edge e has 2 * same_class_pairs * P(S - 2, n - 2) configurations
        ways_per_edge = 2 * same_class_pairs * nPr(S - 2, n - 2)
        S_1 = E_count * ways_per_edge
    else:
        ways_per_edge = 0
        S_1 = 0
        
    # 3. Second-order PIE calculation S_2
    # Incident edge pairs (sharing a vertex v): count = sum_{v} (deg(v) choose 2)
    incident_pairs_count = sum(nCr(G_seats.degree(v), 2) for v in G_seats.nodes())
    disjoint_pairs_count = nCr(E_count, 2) - incident_pairs_count
    
    # Incident edges: path of 3 seats, requires 3 students of same class
    triplet_class_counts = sum(nCr(cnt, 3) for cnt in class_counts.values())
    if n >= 3 and S >= 3:
        incident_ways = 6 * triplet_class_counts * nPr(S - 3, n - 3)
    else:
        incident_ways = 0
        
    # Disjoint edges: 4 seats
    # Case A: 4 students from same class (3 * 4 = 12 ways per disjoint pair)
    quad_class_counts = sum(nCr(cnt, 4) for cnt in class_counts.values())
    # Case B: 2 students from class c1, 2 students from class c2 (c1 != c2) (4 ways per disjoint pair)
    classes_list = list(class_counts.values())
    cross_pairs_count = 0
    for i in range(len(classes_list)):
        for j in range(i + 1, len(classes_list)):
            cross_pairs_count += nCr(classes_list[i], 2) * nCr(classes_list[j], 2)
            
    if n >= 4 and S >= 4:
        disjoint_same_class = 12 * quad_class_counts * nPr(S - 4, n - 4)
        disjoint_cross_class = 4 * cross_pairs_count * nPr(S - 4, n - 4)
        disjoint_ways = disjoint_same_class + disjoint_cross_class
    else:
        disjoint_ways = 0
        
    S_2 = (incident_pairs_count * incident_ways) + (disjoint_pairs_count * disjoint_ways)
    
    # Bonferroni bounds
    lower_bound = max(0, S_1 - S_2)
    upper_bound = min(total_arrangements, S_1)
    
    violation_ratio_upper = (upper_bound / total_arrangements) if total_arrangements > 0 else 0
    violation_ratio_lower = (lower_bound / total_arrangements) if total_arrangements > 0 else 0
    
    # LaTeX formulas for academic report presentation
    product_rule_latex = (
        r"P(S, n) = \frac{S!}{(S - n)!} = "
        f"\\frac{{{S}!}}{{{S - n}!}} = {format_large_number(total_arrangements)}"
    )
    
    pie_latex = (
        r"N(\ge 1) = \left|\bigcup_{e \in E} A_e\right| = "
        r"\sum_{e \in E} |A_e| - \sum_{e < e'} |A_e \cap A_{e'}| + \dots"
    )
    
    bonferroni_latex = (
        rf"S_1 - S_2 \le N(\ge 1) \le S_1 \implies "
        rf"{format_large_number(lower_bound)} \le N(\ge 1) \le {format_large_number(upper_bound)}"
    )
    
    return {
        "n_students": n,
        "total_seats": S,
        "total_edges": E_count,
        "total_arrangements": total_arrangements,
        "total_arrangements_formatted": format_large_number(total_arrangements),
        "product_rule_formula": f"P({S}, {n}) = {S}! / ({S} - {n})!",
        "product_rule_latex": product_rule_latex,
        "pie_latex": pie_latex,
        "bonferroni_latex": bonferroni_latex,
        "S_1_value": S_1,
        "S_1_formatted": format_large_number(S_1),
        "S_2_value": S_2,
        "S_2_formatted": format_large_number(S_2),
        "incident_edge_pairs": incident_pairs_count,
        "disjoint_edge_pairs": disjoint_pairs_count,
        "lower_bound_formatted": format_large_number(lower_bound),
        "upper_bound_formatted": format_large_number(upper_bound),
        "violation_ratio_estimate": f"{min(100.0, violation_ratio_upper * 100):.2f}%",
        "same_class_pairs_count": same_class_pairs,
        "explanation": [
            f"1. Total Unconstrained Assignments: By the Product Rule, placing {n} distinct students into {S} distinct seats yields P({S}, {n}) = {format_large_number(total_arrangements)} total configurations.",
            f"2. Property Definition: For each of the {E_count} physical seat adjacency edges e = (u, v), let A_e be the subset of configurations where both seats u and v are assigned students of the same class.",
            f"3. First-Order Sum (S_1): There are {same_class_pairs} ways to choose two students of the same class. Each edge can host them in 2! = 2 orderings, leaving P({S - 2}, {n - 2}) ways to arrange the remaining {n - 2} students. Summing across all {E_count} edges gives S_1 = {format_large_number(S_1)}.",
            f"4. Second-Order Correction (S_2): Corrects for double-counting across {incident_pairs_count} incident edge pairs (paths of length 2) and {disjoint_pairs_count} disjoint edge pairs.",
            f"5. Bonferroni Bounds: Demonstrates that between {format_large_number(lower_bound)} and {format_large_number(upper_bound)} arrangements violate at least one hard class adjacency constraint."
        ]
    }
