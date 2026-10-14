"""
Implements: Vertex Coloring, Handshaking Lemma (Euler's Degree-Sum Formula), and
Chromatic Polynomial computation (deletion-contraction / SymPy).

Discrete Mathematics Core:
- Handshaking Lemma (Euler, 1736): For any undirected graph G = (V, E),
    sum_{v in V} deg(v) = 2 * |E|
  The sum of all vertex degrees is strictly twice the number of edges. Consequently,
  the number of vertices of odd degree must be even.
- Conflict Graph Coloring: Construct G_conflict where V = students and (u, v) in E
  iff class(u) == class(v). The chromatic number chi(G_conflict) equals max |C_k|,
  and any proper vertex coloring partitions students into color classes such that
  each color class contains students of mutually distinct classes.
- Chromatic Polynomial P(G, lambda): Counts the number of proper vertex colorings of
  the seat adjacency graph using lambda available colors.
  Governed by the Deletion-Contraction Theorem:
    P(G, lambda) = P(G - e, lambda) - P(G / e, lambda)
"""

import networkx as nx
import sympy as sp


def build_student_conflict_graph(students):
    """
    Constructs G_conflict = (V, E) where:
    - V = students
    - E = pairs (u, v) where class(u) == class(v) (same class conflict)
    """
    G = nx.Graph()
    for s in students:
        G.add_node(
            s["id"],
            student_id=s["id"],
            cls=s.get("class", "DefaultClass"),
            subject=s.get("subject", "General"),
            special_needs=s.get("special_needs", False)
        )
    
    # Add edges between students in the same class
    student_list = list(G.nodes(data=True))
    for i in range(len(student_list)):
        for j in range(i + 1, len(student_list)):
            u_id, u_data = student_list[i]
            v_id, v_data = student_list[j]
            if u_data["cls"] == v_data["cls"]:
                G.add_edge(u_id, v_id)
                
    return G


def verify_handshaking_lemma(G):
    """
    Verifies the Handshaking Lemma: sum_{v in V} deg(v) = 2 * |E|.
    Returns full degree list, sum of degrees, edge count, and boolean verification.
    """
    degree_dict = dict(G.degree())
    sum_degrees = sum(degree_dict.values())
    num_edges = G.number_of_edges()
    num_vertices = G.number_of_nodes()
    
    handshaking_holds = (sum_degrees == 2 * num_edges)
    
    odd_degree_vertices = [v for v, d in degree_dict.items() if d % 2 != 0]
    even_odd_count = (len(odd_degree_vertices) % 2 == 0)
    
    # Degrees distribution / top degrees
    degree_list = [
        {"student_id": v, "degree": d, "class": G.nodes[v].get("cls", "N/A")}
        for v, d in sorted(degree_dict.items(), key=lambda x: x[1], reverse=True)
    ]
    
    return {
        "num_vertices": num_vertices,
        "num_edges": num_edges,
        "sum_degrees": sum_degrees,
        "two_times_edges": 2 * num_edges,
        "handshaking_holds": handshaking_holds,
        "odd_degree_vertices_count": len(odd_degree_vertices),
        "even_odd_count_property": even_odd_count,
        "degree_list": degree_list[:25],  # Top 25 for UI presentation
        "full_degree_dict": degree_dict
    }


def color_conflict_graph(G_conflict):
    """
    Partitions the student conflict graph into color classes using NetworkX greedy_color.
    Students with the same color belong to different classes (independent set in G_conflict).
    """
    if len(G_conflict) == 0:
        return {}, 0, {}
    
    # Use largest_first heuristic to optimize coloring order
    coloring = nx.coloring.greedy_color(G_conflict, strategy="largest_first")
    num_colors = max(coloring.values()) + 1 if coloring else 0
    
    # Group students by assigned color class
    color_classes = {}
    for student_id, color_id in coloring.items():
        color_classes.setdefault(color_id, []).append(student_id)
        
    return coloring, num_colors, color_classes


def compute_chromatic_polynomial_symbolic(G):
    """
    Computes the exact chromatic polynomial P(G, lambda) using the Deletion-Contraction
    recurrence algorithm via SymPy:
      P(G, lambda) = P(G - e, lambda) - P(G / e, lambda)
    Optimized for small graphs (<= 10 vertices).
    """
    lam = sp.Symbol('lambda')
    
    def deletion_contraction(graph):
        n = graph.number_of_nodes()
        m = graph.number_of_edges()
        
        # Base case 1: Empty graph (no edges) -> lambda^n
        if m == 0:
            return lam ** n
        
        # Base case 2: Complete graph K_n -> lambda * (lambda - 1) * ... * (lambda - n + 1)
        if m == n * (n - 1) // 2:
            poly = sp.Integer(1)
            for i in range(n):
                poly *= (lam - i)
            return sp.expand(poly)
            
        # Base case 3: Tree on n vertices -> lambda * (lambda - 1)^(n - 1)
        if nx.is_tree(graph):
            return sp.expand(lam * ((lam - 1) ** (n - 1)))
        
        # Pick an edge e = (u, v)
        u, v = next(iter(graph.edges()))
        
        # 1. Deletion: G - e
        G_del = graph.copy()
        G_del.remove_edge(u, v)
        poly_del = deletion_contraction(G_del)
        
        # 2. Contraction: G / e (merge u and v)
        G_con = nx.contracted_nodes(graph, u, v, self_loops=False)
        poly_con = deletion_contraction(G_con)
        
        return sp.expand(poly_del - poly_con)

    poly = deletion_contraction(G)
    return sp.expand(poly)


def get_small_worked_chromatic_polynomial():
    """
    Generates a rigorous, fully worked academic example of the Chromatic Polynomial
    for a standard 2 x 2 exam bench grid (a 4-cycle C_4).
    
    Step-by-step Deletion-Contraction derivation:
    - 4 seats: s1, s2, s3, s4 in a 2x2 grid forming C_4.
    - Edges: (s1, s2), (s2, s4), (s4, s3), (s3, s1).
    - P(C_4, lambda) = (lambda - 1)^4 + (lambda - 1)
                     = lambda^4 - 4*lambda^3 + 6*lambda^2 - 3*lambda
                     = lambda * (lambda - 1) * (lambda^2 - 3*lambda + 3)
    """
    G_c4 = nx.cycle_graph(4)
    lam = sp.Symbol('lambda')
    
    # Exact computation via SymPy
    poly = compute_chromatic_polynomial_symbolic(G_c4)
    factorized = sp.factor(poly)
    
    # Evaluate for lambda = 1, 2, 3, 4 colors
    evaluations = {
        k: int(poly.subs(lam, k))
        for k in [1, 2, 3, 4]
    }
    
    derivation_steps = [
        "1. Define the 2x2 seat cluster graph G = C_4 with V = {s1, s2, s3, s4} and E = {(s1,s2), (s2,s4), (s4,s3), (s3,s1)}.",
        "2. Apply Deletion-Contraction Theorem: P(G, lambda) = P(G - e, lambda) - P(G / e, lambda) on edge e = (s3, s1).",
        "3. G - e is a Path graph P_4 on 4 vertices: P(P_4, lambda) = lambda * (lambda - 1)^3.",
        "4. G / e contracts edge (s3, s1) into a single vertex, yielding a Triangle K_3 on 3 vertices: P(K_3, lambda) = lambda * (lambda - 1) * (lambda - 2).",
        "5. Subtract: P(C_4, lambda) = lambda*(lambda - 1)^3 - lambda*(lambda - 1)*(lambda - 2) = (lambda - 1) * [lambda*(lambda - 1)^2 - lambda*(lambda - 2)].",
        "6. Simplifying yields P(C_4, lambda) = lambda^4 - 4*lambda^3 + 6*lambda^2 - 3*lambda = lambda * (lambda - 1) * (lambda^2 - 3*lambda + 3)."
    ]
    
    return {
        "example_name": "Standard 2x2 Bench Unit Cell (4-Cycle C_4)",
        "vertices_count": 4,
        "edges_count": 4,
        "polynomial_latex": sp.latex(poly),
        "polynomial_str": str(poly),
        "factorized_latex": sp.latex(factorized),
        "factorized_str": str(factorized),
        "evaluations": evaluations,
        "derivation_steps": derivation_steps
    }
