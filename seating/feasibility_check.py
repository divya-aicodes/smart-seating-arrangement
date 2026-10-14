"""
Implements: Dirichlet's Box Principle / Pigeonhole Principle (PHP), Maximum Independent
Set bounds, and Local Degree Saturation for feasibility pre-check.

Discrete Mathematics Core:
- Generalized Pigeonhole Principle: If n items are placed into k boxes, then at least
  one box must contain at least ceil(n/k) items.
- Maximum Independent Set alpha(G): In the room adjacency graph G, any set of seats
  allocated to the same class must form an independent set (no two adjacent). Therefore,
  for any class C_i, if |C_i| > alpha(G), a collision is mathematically inevitable.
- Local Degree Saturation: For any seat v with degree d(v), the closed neighborhood
  N[v] has size d(v) + 1. If the remaining available classes or subjects are fewer
  than required by the local density, an adjacency collision is forced.
"""

from collections import Counter
import networkx as nx
import math


def build_seat_adjacency_graph(room_configs, diagonal=False):
    """
    Constructs a NetworkX graph G = (V, E) where:
    - V = set of all seats across all configured rooms
    - E = pairs of seats that are physically adjacent (orthogonal or optional diagonal)
    
    Each node has attributes:
      room_id, row, bench, seat_in_bench, seat_label, accessible
    """
    G = nx.Graph()
    
    for room in room_configs:
        room_id = room.get("room_id", 1)
        rows = room.get("rows", 4)
        cols = room.get("cols", 3)
        seats_per_bench = room.get("seats_per_bench", 2)
        accessible_seats = set(room.get("accessible_seats", []))
        unavailable_seats = set(room.get("unavailable_seats", []))
        
        # Grid dimensions: rows x (cols * seats_per_bench)
        total_cols_seats = cols * seats_per_bench
        
        for r in range(1, rows + 1):
            for c in range(1, cols + 1):
                for s in range(1, seats_per_bench + 1):
                    seat_x = (c - 1) * seats_per_bench + s
                    seat_id = f"R{room_id}-r{r}-c{c}-s{s}"
                    if seat_id in unavailable_seats:
                        continue
                    seat_label = f"Room {room_id} [Row {r}, Bench {c}, Seat {s}]"
                    
                    is_accessible = False
                    if seat_id in accessible_seats:
                        is_accessible = True
                    elif "row_1" in accessible_seats and r == 1:
                        is_accessible = True
                    elif "aisle" in accessible_seats and (s == 1 or s == seats_per_bench):
                        is_accessible = True
                        
                    G.add_node(
                        seat_id,
                        room_id=room_id,
                        row=r,
                        bench=c,
                        seat_in_bench=s,
                        col_idx=seat_x,
                        seat_label=seat_label,
                        accessible=is_accessible
                    )
        
        # Add adjacency edges for this room
        room_nodes = [n for n, d in G.nodes(data=True) if d.get("room_id") == room_id]
        node_map = {(d["row"], d["col_idx"]): n for n, d in G.nodes(data=True) if d.get("room_id") == room_id}
        
        for r in range(1, rows + 1):
            for x in range(1, total_cols_seats + 1):
                u = node_map.get((r, x))
                if not u:
                    continue
                
                # Orthogonal: Horizontal (same row, adjacent seat)
                v_right = node_map.get((r, x + 1))
                if v_right:
                    G.add_edge(u, v_right, type="horizontal")
                
                # Orthogonal: Vertical (front / back)
                v_down = node_map.get((r + 1, x))
                if v_down:
                    G.add_edge(u, v_down, type="vertical")
                
                # Optional: Diagonal
                if diagonal:
                    v_diag1 = node_map.get((r + 1, x + 1))
                    if v_diag1:
                        G.add_edge(u, v_diag1, type="diagonal")
                    v_diag2 = node_map.get((r + 1, x - 1))
                    if v_diag2:
                        G.add_edge(u, v_diag2, type="diagonal")

    return G


def compute_independence_number_bound(G):
    """
    Computes/bounds the independence number alpha(G).
    For bipartite graphs (e.g. orthogonal grids), alpha(G) is exact via max matching:
    alpha(G) = |V| - max_matching(G) (Konig's Theorem).
    For non-bipartite graphs, provides an accurate heuristic or exact maximal independent set.
    """
    if len(G) == 0:
        return 0
    if nx.is_bipartite(G):
        matching = nx.bipartite.maximum_matching(G)
        # matching includes both directions (u->v, v->u), so divide by 2
        return len(G) - (len(matching) // 2)
    else:
        # Maximal independent set approximation
        mis = nx.maximal_independent_set(G)
        return len(mis)


def check_feasibility(students, room_configs, diagonal=False):
    """
    Performs comprehensive pre-assignment feasibility analysis using:
    1. Capacity bounds (|Students| <= |Seats|)
    2. Global Pigeonhole Principle on Maximum Independent Set of the graph
    3. Local Degree Saturation on Closed Neighborhoods N[v]
    4. Accessibility capacity check (|SpecialNeeds| <= |AccessibleSeats|)

    Returns a detailed dictionary of findings with human-readable diagnostic messages.
    """
    diagnostics = []
    warnings = []
    is_feasible = True
    hard_violation_guaranteed = False
    
    total_students = len(students)
    G_seats = build_seat_adjacency_graph(room_configs, diagonal=diagonal)
    total_seats = G_seats.number_of_nodes()
    
    # 1. Total capacity check
    if total_students > total_seats:
        is_feasible = False
        hard_violation_guaranteed = True
        msg = (
            f"CRITICAL OVER-CAPACITY: Total students ({total_students}) exceeds total available "
            f"seats ({total_seats}) across {len(room_configs)} room(s). "
            f"Deficit of {total_students - total_seats} seats."
        )
        diagnostics.append({"type": "error", "code": "CAPACITY_EXCEEDED", "message": msg})
    
    # 2. Accessible seats check (Forbidden Positions Pre-Check)
    accessible_seat_ids = [n for n, d in G_seats.nodes(data=True) if d.get("accessible", False)]
    special_needs_students = [s for s in students if s.get("special_needs", False)]
    
    if len(special_needs_students) > len(accessible_seat_ids):
        is_feasible = False
        hard_violation_guaranteed = True
        msg = (
            f"ACCESSIBILITY VIOLATION: There are {len(special_needs_students)} special-needs "
            f"students, but only {len(accessible_seat_ids)} designated accessible seats available. "
            f"Forbidden-position constraints cannot be satisfied."
        )
        diagnostics.append({"type": "error", "code": "INSUFFICIENT_ACCESSIBLE_SEATS", "message": msg})
    
    # 3. Global Pigeonhole Principle (PHP) check on Classes (Hard Constraint)
    class_counts = Counter(s.get("class", "Unknown") for s in students)
    if diagonal:
        # A king-grid independent set can occupy at most every other row and column.
        # Rooms are disconnected components, so their exact maxima add together.
        alpha_G = sum(
            math.ceil(room.get("rows", 4) / 2)
            * math.ceil((room.get("cols", 3) * room.get("seats_per_bench", 2)) / 2)
            for room in room_configs
        )
    else:
        alpha_G = compute_independence_number_bound(G_seats)
    
    class_analysis = []
    for cls_name, count in class_counts.most_common():
        ratio = count / alpha_G if alpha_G > 0 else 1.0
        exceeds_alpha = count > alpha_G
        class_info = {
            "class": cls_name,
            "count": count,
            "max_independent_bound": alpha_G,
            "ratio": round(ratio, 3),
            "guaranteed_collision": exceeds_alpha
        }
        class_analysis.append(class_info)
        
        if exceeds_alpha:
            is_feasible = False
            hard_violation_guaranteed = True
            msg = (
                f"PIGEONHOLE HARD CONSTRAINT VIOLATION: Class '{cls_name}' has {count} students, "
                f"which strictly exceeds the room graph's maximum independent set bound alpha(G) = {alpha_G}. "
                f"By the Pigeonhole Principle, at least ceil({count} / {alpha_G}) = {math.ceil(count / alpha_G)} "
                f"students of class '{cls_name}' MUST be placed in mutually adjacent seats."
            )
            diagnostics.append({"type": "error", "code": "PHP_CLASS_COLLISION", "message": msg})
    
    # 4. Local Neighborhood & Degree Saturation Check
    # For each seat v, d(v) = degree. If students of a dominant class or subject occupy a large fraction,
    # check if high-degree seats necessarily force collisions.
    subject_counts = Counter(s.get("subject", "General") for s in students)
    num_classes = len(class_counts)
    num_subjects = len(subject_counts)
    
    high_risk_seats = []
    for seat_id, d in G_seats.nodes(data=True):
        degree = G_seats.degree(seat_id)
        closed_neighborhood_size = degree + 1
        
        # Check if local seat degree forces subject collisions (Soft constraint warning)
        # If any subject represents > 50% of the population, a seat with degree >= 2
        # is statistically and structurally prone to subject collisions.
        for subj_name, s_count in subject_counts.items():
            subj_fraction = s_count / total_students if total_students > 0 else 0
            # If expected same-subject occurrences in closed neighborhood > 1
            if closed_neighborhood_size * subj_fraction > 1.4 and degree >= 2:
                warning_text = (
                    f"Seat {d['seat_label']} has {degree} neighbors (closed neighborhood |N[v]| = {closed_neighborhood_size}). "
                    f"Given subject '{subj_name}' accounts for {subj_fraction * 100:.1f}% of students, "
                    f"the Pigeonhole Principle indicates a subject collision is likely or unavoidable here."
                )
                high_risk_seats.append({
                    "seat_id": seat_id,
                    "label": d["seat_label"],
                    "degree": degree,
                    "subject": subj_name,
                    "fraction": round(subj_fraction, 3),
                    "warning": warning_text
                })
                warnings.append(warning_text)
                break  # Don't duplicate for multiple subjects on same seat
    
    # Summary report
    status_summary = {
        "is_feasible": is_feasible,
        "hard_violation_guaranteed": hard_violation_guaranteed,
        "total_students": total_students,
        "total_seats": total_seats,
        "seat_graph_nodes": total_seats,
        "seat_graph_edges": G_seats.number_of_edges(),
        "independence_number_alpha": alpha_G,
        "class_analysis": class_analysis,
        "subject_distribution": dict(subject_counts),
        "diagnostics": diagnostics,
        "warnings": warnings[:12],  # Cap top prominent warnings to avoid UI overflow
        "total_warnings_count": len(warnings),
        "high_risk_seats": high_risk_seats[:10],
        "special_needs_count": len(special_needs_students),
        "accessible_seats_count": len(accessible_seat_ids)
    }
    
    return status_summary, G_seats
