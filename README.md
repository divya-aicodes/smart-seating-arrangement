# SeatPlan — Examination Operations

SeatPlan is a database-backed Flask application for preparing, validating, and
auditing examination seating plans. It accepts institutional CSV data or
reproducible synthetic datasets, represents rooms as seat-adjacency graphs,
enforces hard seating constraints, minimizes soft subject conflicts, persists
eligible runs, and produces operational reports.

**Live application:** [smart-seating-arrangement-tan.vercel.app](https://smart-seating-arrangement-tan.vercel.app/)

**Source repository:** [github.com/divya-aicodes/smart-seating-arrangement](https://github.com/divya-aicodes/smart-seating-arrangement)

## Table of contents

- [Problem statement](#problem-statement)
- [Objectives and scope](#objectives-and-scope)
- [Application features](#application-features)
- [End-to-end workflow](#end-to-end-workflow)
- [Mathematical model](#mathematical-model)
- [Solver pipeline](#solver-pipeline)
- [Data sources and persistence](#data-sources-and-persistence)
- [System architecture](#system-architecture)
- [Technology stack](#technology-stack)
- [Database model](#database-model)
- [Web pages and routes](#web-pages-and-routes)
- [REST API](#rest-api)
- [Frontend design](#frontend-design)
- [Security model](#security-model)
- [Project structure](#project-structure)
- [Local development](#local-development)
- [Testing](#testing)
- [Vercel deployment](#vercel-deployment)
- [Current limitations](#current-limitations)

## Problem statement

Manual examination seating is difficult to audit and becomes error-prone when
administrators must place many students across rooms while respecting physical
capacity, accessibility, class separation, subject separation, unavailable
seats, and invigilator requirements.

The engineering problem is to construct an injective mapping

$$
f:S\rightarrow V
$$

from the set of students $S$ to the set of available seat vertices $V$, such
that all hard constraints are satisfied and avoidable soft conflicts are
minimized. The result must also be explainable, reproducible, persistable, and
exportable for operational use.

SeatPlan converts each room into a graph, rejects provably infeasible inputs
before allocation, places accessibility-constrained students first, applies a
strict class-safe greedy mapper, and validates every occupied adjacency edge
before a result is accepted.

## Objectives and scope

The application is designed to:

1. use real or reproducibly generated data instead of static frontend arrays;
2. prevent over-capacity, inaccessible placement, duplicate seating, and
   adjacent same-class placement;
3. reduce adjacent same-subject placement when a zero-conflict arrangement is
   available to the current heuristic;
4. explain the result with discrete-mathematics reports and a validation
   certificate;
5. preserve dataset lineage across exams, rooms, seats, students, runs, and
   exports;
6. provide browser, API, CSV, XLSX, and PDF workflows; and
7. protect state-changing requests with CSRF, origin checks, input validation,
   and production configuration guards.

### Optimization boundary

SeatPlan does **not** claim to calculate a globally optimal arrangement. The
current allocator is a deterministic greedy heuristic with strict hard
constraint rejection and a complete post-allocation validation pass.

- Graph theory is the correct model because the problem is defined by pairwise
  seat adjacency and student conflicts.
- K-means clustering is not used. K-means minimizes distance to numerical
  centroids and does not naturally enforce adjacency, accessibility, uniqueness,
  or room-capacity constraints.
- If globally optimal solutions or formal proofs of unsatisfiability are needed
  at larger scales, the mapper can be replaced by a CP-SAT, integer-programming,
  or SAT solver while retaining the existing data and validation layers.

## Application features

### Planning

- Select a persisted exam and rooms from one versioned dataset.
- Run an ad hoc plan from an uploaded student CSV file.
- Choose orthogonal or orthogonal-plus-diagonal adjacency.
- Configure one to ten manual rooms and their physical layout.
- Mark front-row, aisle, combined, or front-aisle-only accessible positions.
- Configure the student-to-invigilator ratio.
- Download a header-only CSV import template.

### Data management

- Import real student, subject, and exam records from UTF-8 CSV.
- Generate reproducible synthetic datasets from a numeric seed.
- Create rooms and materialize their individual seat records.
- Preserve source type (`real` or `synthetic`), dataset version, and lineage.
- Record imports, validation failures, and material state changes in the audit log.

### Results and reporting

- Display room-by-room visual seat maps.
- Show a flat, searchable allocation table.
- Display feasibility, graph, coloring, logical, and combinatorial reports.
- Identify relaxable same-subject adjacency pairs.
- Calculate invigilators required for each room.
- Export the immediate result as CSV.
- Export persisted runs as PDF and XLSX.
- Publish completed persisted runs through the API.

## End-to-end workflow

```text
1. Create or import a dataset
          |
2. Create an exam, registrations, rooms, and seats
          |
3. Select connected records or one-time CSV mode
          |
4. Upload, preview, and validate the student CSV
          |
5. Calculate room-layout options from the CSV distribution
          |
6. Apply a recommended setup or enter the physical setup manually
          |
7. Build the seat-adjacency graph and reject provably infeasible inputs
          |
8. Place accessibility-constrained students
          |
9. Build the student conflict graph and map remaining students
          |
10. Validate every hard and soft predicate
          |
11. Persist eligible runs, then review maps, mathematics, and exports
```

Database mode connects the complete workflow to persisted records. Manual mode
produces immediate results and an audit event, but does not create a persisted
`SeatingRun`.

## Mathematical model

### Notation

| Symbol | Definition |
| --- | --- |
| $S$ | Set of students |
| $n=|S|$ | Number of students |
| $G_s=(V_s,E_s)$ | Undirected physical seat-adjacency graph |
| $V_s$ | Active and available seats |
| $E_s$ | Pairs of physically adjacent seats |
| $G_c=(V_c,E_c)$ | Student conflict graph |
| $C_i$ | Students belonging to class or section $i$ |
| $d(v)$ | Degree, or number of adjacent seats, of vertex $v$ |
| $N[v]$ | Closed neighborhood of $v$, including $v$ itself |
| $\alpha(G)$ | Maximum set of pairwise non-adjacent vertices |
| $\nu(G)$ | Maximum-matching size |
| $\chi(G)$ | Chromatic number |
| $P(G,\lambda)$ | Chromatic polynomial |

### 1. Seat-adjacency graph

Every available seat is a vertex. An edge joins seats that must be checked
against each other. For orthogonal mode:

$$
E_s=\{(u,v):u\text{ and }v\text{ are horizontal or vertical neighbors}\}
$$

Diagonal mode also adds the two diagonal neighbors in the following row.
Unavailable seats are omitted from $V_s$, and multiple rooms form disconnected
components of the same graph.

For a room with $r$ rows, $b$ benches per row, and $q$ seats per bench:

$$
\operatorname{capacity}=r\cdot b\cdot q
$$

### 2. Capacity feasibility

An injective student-to-seat mapping is impossible when:

$$
|S|>|V_s|
$$

The application treats this as a guaranteed hard failure and reports the exact
seat deficit before attempting allocation.

### 3. Generalized Pigeonhole Principle

Placing $n$ objects into $k$ boxes forces at least one box to contain:

$$
\left\lceil\frac{n}{k}\right\rceil
$$

objects. In SeatPlan, all students from one class must occupy an independent
set in the seat graph. Class $C_i$ is impossible to place without adjacency when:

$$
|C_i|>\alpha(G_s)
$$

For orthogonal room grids, $G_s$ is bipartite. By the Kőnig–Egérváry
relationship:

$$
\alpha(G_s)=|V_s|-\nu(G_s)
$$

where $\nu(G_s)$ is computed through maximum bipartite matching.

For diagonal king-grid adjacency, the exact room-grid bound is:

$$
\alpha(G_s)=
\sum_{\text{room }j}
\left\lceil\frac{r_j}{2}\right\rceil
\cdot
\left\lceil\frac{b_jq_j}{2}\right\rceil
$$

Exceeding this bound proves infeasibility. Staying below it does not prove that
the current greedy mapper will find a placement.

### 4. Local degree-saturation warning

For a seat $v$:

$$
|N[v]|=d(v)+1
$$

If subject $j$ represents proportion $p_j$ of the population, the application
flags a local risk when:

$$
|N[v]|\cdot p_j>1.4,\qquad d(v)\ge 2
$$

This is a **heuristic warning**, not proof of a collision. It identifies dense
neighborhoods exposed to a dominant subject.

### 5. Forbidden positions and accessibility

For each accessibility-constrained student $x_i$, every non-accessible seat is
a forbidden position. If $A\subseteq V_s$ is the accessible-seat set:

$$
|\{s\in S:\operatorname{special}(s)\}|\le |A|
$$

The conceptual forbidden board is:

$$
B=\bigcup_i \left(\{x_i\}\times (V_s\setminus A)\right)
$$

The classical rook polynomial is:

$$
R(x,B)=\sum_{k=0}^{m}r_k(B)x^k
$$

where $r_k(B)$ counts placements of $k$ non-attacking rooks on forbidden cells.
SeatPlan reports board dimensions and cell counts, but does not numerically
expand the rook polynomial. The implemented allocator backtracks over the
complementary board $S\times A$ and also rejects adjacent same-class placements.

For $m$ constrained students, $a$ accessible seats, and $N=|V_s|$:

$$
\begin{aligned}
\text{total board cells} &= mN\\
\text{forbidden cells} &= m(N-a)\\
\text{permitted cells} &= ma
\end{aligned}
$$

### 6. Student conflict graph

The conflict graph contains one vertex per student:

$$
G_c=(V_c,E_c),\qquad V_c=S
$$

An edge joins students from the same class:

$$
(u,v)\in E_c\iff \operatorname{class}(u)=\operatorname{class}(v)
$$

Each class induces a clique. This graph is used for diagnostics and coloring;
final seat placement is separately enforced against $G_s$.

### 7. Handshaking Lemma

For every finite undirected graph:

$$
\sum_{v\in V_c}d(v)=2|E_c|
$$

The report calculates every student degree, the degree sum, twice the edge
count, and the number of odd-degree vertices. It verifies the identity and the
consequence that an undirected graph has an even number of odd-degree vertices.

### 8. Graph coloring

A proper coloring satisfies:

$$
c:V_c\rightarrow\{1,\ldots,k\},\qquad
(u,v)\in E_c\Rightarrow c(u)\ne c(v)
$$

The application uses NetworkX `greedy_color` with `largest_first`. Because each
class is a clique, its students receive different colors. This is an explanatory
partition, not a claim of globally optimal seat placement.

### 9. Chromatic polynomial

$P(G,\lambda)$ counts proper colorings using $\lambda$ colors and follows:

$$
P(G,\lambda)=P(G-e,\lambda)-P(G/e,\lambda)
$$

SeatPlan computes a fixed, exact worked example for the four-seat cycle $C_4$:

$$
\begin{aligned}
P(C_4,\lambda)
&=(\lambda-1)^4+(\lambda-1)\\
&=\lambda^4-4\lambda^3+6\lambda^2-3\lambda\\
&=\lambda(\lambda-1)(\lambda^2-3\lambda+3)
\end{aligned}
$$

It does not run exponential deletion-contraction over the full production graph.

### 10. Greedy seat-mapping objective

Students are interleaved by class size. A candidate seat adjacent to the same
class is rejected. For hard-valid seats, the soft score is:

$$
\operatorname{cost}(v,s)=
10\sum_{u\in N(v)}
\mathbf{1}\left[
\operatorname{subject}(s)=\operatorname{subject}(f^{-1}(u))
\right]
$$

The available seat with the smallest score is selected. A zero-cost candidate
is accepted immediately. Same-subject adjacency is a reported soft relaxation;
same-class adjacency is never a fallback.

### 11. Formal logical validation

After allocation, every undirected edge is checked against four predicates.

$$
\varphi_1:\forall(u,v)\in E_s,\quad
(\operatorname{occupied}(u)\land\operatorname{occupied}(v))
\Rightarrow\operatorname{class}(u)\ne\operatorname{class}(v)
$$

$$
\varphi_2:\forall(u,v)\in E_s,\quad
(\operatorname{occupied}(u)\land\operatorname{occupied}(v))
\Rightarrow\operatorname{subject}(u)\ne\operatorname{subject}(v)
$$

$$
\varphi_3:\forall s\in S,\quad
\operatorname{special}(s)\Rightarrow\operatorname{accessible}(f(s))
$$

$$
\varphi_4:\forall s_1,s_2\in S,\quad
s_1\ne s_2\Rightarrow f(s_1)\ne f(s_2)
$$

The result is accepted only when:

$$
\varphi_1\land\varphi_3\land\varphi_4=\text{true}
$$

$\varphi_2$ is soft, so violations remain explicit relaxation records.

### 12. Product Rule

For $n$ students and $N=|V_s|$ distinct seats:

$$
P(N,n)=\frac{N!}{(N-n)!}
$$

This describes the unrestricted search space; the solver does not enumerate it.

### 13. Principle of Inclusion-Exclusion

For every seat edge $e$, let $A_e$ mean both endpoints contain the same class:

$$
\left|\bigcup_{e\in E_s}A_e\right|=S_1-S_2+S_3-\cdots
$$

Let:

$$
Q_2=\sum_c\binom{|C_c|}{2}
$$

The implemented first-order term is:

$$
S_1=|E_s|\cdot 2Q_2\cdot P(N-2,n-2)
$$

The second-order correction separates incident and disjoint edge pairs:

$$
I=\sum_{v\in V_s}\binom{d(v)}{2},
\qquad
D=\binom{|E_s|}{2}-I
$$

The Bonferroni bounds are:

$$
\max(0,S_1-S_2)
\le\left|\bigcup_{e\in E_s}A_e\right|
\le\min(P(N,n),S_1)
$$

These are theoretical counts, not an accuracy percentage for the heuristic.

### 14. Invigilator requirement

For $n_r$ assigned students in a room and configured ratio $q$:

$$
\operatorname{invigilators}(r)=
\begin{cases}
0,&n_r=0\\
\max\left(1,\left\lceil\frac{n_r}{q}\right\rceil\right),&n_r>0
\end{cases}
$$

Immediate result labels are placeholders; persisted staff assignment is modeled
separately.

## Solver pipeline

| Phase | Implementation | Output or failure behavior |
| --- | --- | --- |
| 1. Feasibility | Capacity, accessible-seat count, independence bound, and local-risk analysis | Returns HTTP 422 without generating a plan when a hard failure is guaranteed |
| 2. Forbidden positions | Backtracking over accessible seats | Stops if accessibility and class non-adjacency cannot both be satisfied |
| 3. Graph analysis | Student conflict graph, handshaking report, greedy coloring, and worked chromatic polynomial | Produces explanatory mathematics |
| 4. Seat mapping | Class-interleaved greedy placement with subject-conflict scoring | Leaves an impossible student unassigned rather than violating a hard constraint |
| 5. Validation | Full edge scan and logical certificate | Rejects any class, accessibility, or uniqueness failure |
| 6. Counting | Product Rule, first- and second-order inclusion-exclusion, and Bonferroni bounds | Produces theoretical search-space and violation bounds |
| 7. Persistence | Transactional run and allocation creation for database mode | Stores only validated database-backed allocations |

### Complexity notes

- Building the seat graph is $O(|V_s|+|E_s|)$.
- The conflict-graph constructor compares student pairs and is $O(n^2)$.
- Greedy mapping is polynomial but repeatedly evaluates available seats and
  their occupied neighbors; practical cost depends on $n$, $|V_s|$, and degree.
- Accessibility backtracking is exponential in the worst case.
- Exact chromatic-polynomial computation is exponential, which is why the web
  workflow uses it only for the fixed four-vertex example.

## Data sources and persistence

### Real institutional data

The Data workspace accepts a UTF-8 CSV file. Required logical columns are:

- `ID`
- `Class`
- `Subject`

Optional columns are:

- `Name`
- `Department`
- `Subject_Code`
- `Special_Needs`

Example:

```csv
ID,Name,Class,Department,Subject_Code,Subject,Special_Needs
STU-001,Asha Rao,CSE-A,Computer Science,CS301,Algorithms,N
STU-002,Rohan Sen,CSE-B,Computer Science,CS301,Algorithms,Y
STU-003,Meera Das,ECE-A,Electronics,EC204,Signals,N
```

The import limit is 2 MiB. Student identifiers must be unique inside the
dataset. Rows wider than the header, invalid UTF-8, missing required values, and
conflicting identifiers are rejected.

### Synthetic data

Synthetic generation accepts:

- dataset name and version;
- a seed from `0` to `2,147,483,647`;
- student, class, subject, and room counts; and
- exam date and time.

The browser route supports 2–1,000 students, 2–50 classes, 1–20 subjects, and
1–20 rooms. Generation uses `random.Random(seed)`, so accepted parameters are
reproducible without static frontend fixtures.

Real and synthetic records use the same tables, planning workflow, and reports.
Their `source_type` and dataset lineage remain explicit.

### Manual planning data

Manual mode accepts the same student fields from an uploaded CSV and
constructs room geometry from the form. Before generation, the planner sends the
CSV to a CSRF- and origin-protected same-origin endpoint. The backend validates
the records and returns up to three one-click layout choices based on capacity,
largest-class independent-set bounds, accessibility requests, and the selected
adjacency rule. The user also supplies the college's maximum seats per room and
maximum available rooms, so the system does not assume that one oversized room
exists. Each candidate must also complete a dry run through the actual
accessibility assignment, seat mapper, and hard-constraint validator before it
is shown. Choosing a card fills room count, rows, benches per row, and seats per
bench. Manual runs record an audit event but do not create a database
`SeatingRun` or persistent allocations.

### Persisted planning data

A database-backed run requires:

- an exam in `draft` or `scheduled` state;
- eligible or registered active students;
- active rooms and seats; and
- matching dataset ID and source type across the exam and selected rooms.

Successful allocations are committed transactionally. A persistence or hard
constraint failure does not leave a partially completed allocation set.

## System architecture

```text
Browser
  |-- Jinja-rendered pages
  |-- CSS and vanilla JavaScript
  |-- CSRF-protected forms
  |
Flask application
  |-- Planning routes
  |-- Data-management blueprint
  |-- Reports blueprint
  |-- Versioned REST API
  |-- Origin and host guards
  |
Domain services
  |-- Import and synthetic generation
  |-- Graph-based seating engine
  |-- Persistence and audit services
  |-- PDF/XLSX report builders
  |
SQLAlchemy
  |-- SQLite for local development
  +-- PostgreSQL for production
```

The frontend and API share one Flask application and domain model. There is no
disconnected static-data demonstration layer.

## Technology stack

| Area | Technology | Purpose |
| --- | --- | --- |
| Language | Python 3.12+ | Application, solver, reports, and tests |
| Web framework | Flask 3 | Routing, request lifecycle, templates, and responses |
| Templates | Jinja | Server-rendered pages |
| Frontend | Semantic HTML5, modern CSS, vanilla JavaScript | Responsive interface and progressive interaction |
| ORM | Flask-SQLAlchemy / SQLAlchemy | Relational persistence and transactions |
| Migrations | Flask-Migrate / Alembic | Versioned database schema |
| Forms and CSRF | Flask-WTF | CSRF token generation and validation |
| Session utilities | Flask-Login | User/session primitives retained by the backend model |
| Graph mathematics | NetworkX | Graph construction, matching, coloring, and properties |
| Symbolic mathematics | SymPy | Chromatic-polynomial expansion and factorization |
| Spreadsheet reports | OpenPyXL | XLSX generation |
| PDF reports | ReportLab | PDF generation |
| Production database driver | Psycopg 3 | PostgreSQL connectivity |
| WSGI server | Gunicorn | Long-running production platforms |
| Serverless hosting | Vercel Python runtime | Current public deployment |
| Tests | Python `unittest` | Integration, security, persistence, and solver verification |

## Database model

| Entity | Responsibility |
| --- | --- |
| `User` | Backend user and role record; the current public UI has no login or sign-up screen |
| `Dataset` | Versioned real or synthetic source boundary |
| `Student` | Student identity, class, department, and accessibility requirement |
| `Subject` | Subject code, title, and department |
| `Exam` | Scheduled subject session linked to one dataset |
| `ExamRegistration` | Student eligibility for an exam and subject |
| `Room` | Geometry, capacity, building, source type, and active state |
| `Seat` | Materialized row, bench, seat number, accessibility, and active state |
| `SeatingRun` | Solver configuration, lifecycle, validation result, and timestamps |
| `SeatAllocation` | Transactional student-to-seat assignment and relaxation flags |
| `Invigilator` | Persisted staff identity and availability |
| `InvigilatorAssignment` | Staff-to-room/exam assignment |
| `DataImport` | Import provenance, counts, status, and validation messages |
| `AuditLog` | Operational action record |

Important relationships:

```text
Dataset
  |-- Students
  |-- Subjects
  |-- Exams -- ExamRegistrations
  |             +-- SeatingRuns -- SeatAllocations
  |-- Rooms -- Seats -----------^
  |-- Invigilators -- InvigilatorAssignments
  +-- DataImports

AuditLog records relevant actions across these resources.
```

## Web pages and routes

| Method | Route | Purpose |
| --- | --- | --- |
| `GET` | `/` | Plan a seating run from persisted or manual data |
| `POST` | `/api/layout-recommendations` | Validate temporary CSV data and calculate one-click room setups |
| `POST` | `/assign` | Execute the six-stage mathematical planning workflow |
| `GET` | `/sample-csv` | Download a header-only student import template |
| `POST` | `/export-csv` | Export the current flat allocation table |
| `GET` | `/data` | Manage datasets, imports, synthetic data, and rooms |
| `POST` | `/data/import-students` | Persist a real CSV dataset and exam |
| `POST` | `/data/generate-synthetic` | Generate and persist a seeded dataset |
| `POST` | `/data/rooms` | Create a room and its seats |
| `GET` | `/reports` | View completed and published persisted runs |
| `GET` | `/reports/runs/<run_id>` | View one persisted seating report |
| `GET` | `/reports/runs/<run_id>.pdf` | Download PDF report |
| `GET` | `/reports/runs/<run_id>.xlsx` | Download XLSX report |

All project pages open directly; there is no login, sign-in, or sign-up page.

## REST API

The API root is `/api/v1`.

| Method | Endpoint | Purpose |
| --- | --- | --- |
| `GET` | `/api/v1` | API discovery |
| `GET` | `/api/v1/health` | Health response |
| `GET` | `/api/v1/openapi.json` | OpenAPI document |
| `GET` | `/api/v1/datasets` | Paginated dataset list with source filtering |
| `GET` | `/api/v1/datasets/<id>` | Dataset detail |
| `POST` | `/api/v1/datasets/synthetic` | Generate a persisted synthetic dataset |
| `GET` | `/api/v1/exams` | Paginated exam list |
| `GET` | `/api/v1/exams/<id>` | Exam detail |
| `GET` | `/api/v1/rooms` | Paginated room list |
| `GET` | `/api/v1/rooms/<id>` | Room detail |
| `GET` | `/api/v1/seating-runs` | Paginated run list |
| `GET` | `/api/v1/seating-runs/<id>` | Run detail |
| `GET` | `/api/v1/seating-runs/<id>/allocations` | Paginated allocations |
| `POST` | `/api/v1/seating-runs/<id>/publish` | Publish a completed run |

Read endpoints are public in the current scope. State-changing API requests need
a valid CSRF token and must pass the same-origin/exact-origin guard. See
[`API.md`](API.md) for response and error contracts.

## Frontend design

The interface is a professional, light-only operations workspace.

- Light paper, white, navy, engineering-blue, and restrained orange palette.
- Barlow, Barlow Condensed, and IBM Plex Mono typography.
- Responsive planning, data, reports, results, and report-detail pages.
- Mobile navigation without horizontal overflow.
- Semantic labels, tables, buttons, forms, and status messages.
- Visible keyboard focus treatment.
- Short page reveals, status pulses, button movement, and navigation transitions.
- `prefers-reduced-motion` disables non-essential animation.
- Print rules keep result and report content visible.
- No dark mode, neon, glassmorphism, decorative gradients, or unsupported
  “AI-powered” claims.

Client JavaScript improves interaction but never replaces server validation. It
manages source-mode controls, room filtering, capacity previews, file labels,
table search, seat-label toggles, navigation, and submit feedback.

## Security model

### Cross-origin resource sharing

CORS is deny-by-default.

- `CORS_ALLOWED_ORIGINS` accepts comma-separated exact HTTP(S) origins.
- Wildcards, paths, query strings, fragments, and malformed origins are rejected
  during configuration parsing.
- `Access-Control-Allow-Origin` is emitted only for the same origin or an exact
  allowlist match.
- The server never reflects arbitrary origins.
- `CORS_ALLOW_CREDENTIALS` defaults to `false`.
- Credentialed cross-origin sessions require secure cookies and `SameSite=None`.
- State-changing requests from untrusted origins are rejected before business
  logic runs.

For the same-origin Vercel deployment, leave `CORS_ALLOWED_ORIGINS` empty.

### Request and browser protections

- CSRF validation for browser and API state changes.
- Two-hour CSRF token lifetime.
- Secure, HTTP-only session cookie in production.
- `SameSite=Lax` by default.
- Exact production host allowlist.
- 2 MiB request and upload limit.
- Maximum 5,000 students per manual planning run.
- CSV formula-injection neutralization during export.
- Parameter validation and bounded numeric inputs.
- Transaction rollback on persistence failure.
- Generic HTTP 500 responses with server-side exception logging.
- `X-Content-Type-Options: nosniff`.
- `X-Frame-Options: DENY`.
- Strict referrer policy.
- Restricted browser permissions policy.
- `Cache-Control: no-store`.
- HSTS in production.

### Production startup guards

Production refuses to start when:

- `SECRET_KEY` is missing, placeholder-like, or shorter than 32 characters;
- `DATABASE_URL` is missing, uses SQLite, or is not a Psycopg PostgreSQL URL;
- `ALLOWED_HOSTS` is empty; or
- secure session cookies are disabled.

## Project structure

```text
api/
  index.py                    Vercel import entrypoint
seating/
  feasibility_check.py        Capacity, independence, and risk analysis
  forbidden_positions.py     Accessible-seat backtracking
  graph_coloring.py           Conflict graph and coloring mathematics
  seat_mapper.py              Strict greedy seat placement
  validator.py                Formal edge and mapping validation
  counting.py                 Product Rule and inclusion-exclusion
templates/
  base.html                   Shared page shell and navigation
  index.html                  Planning workspace
  data_management.html        Dataset and room operations
  results.html                Seat maps and mathematical audit
  reports_dashboard.html      Persisted-run archive
  report_detail.html          Persisted allocation report
static/
  css/style.css               Light design system and responsive layout
  js/main.js                  Progressive browser interactions
  favicon.svg                 SeatPlan favicon
migrations/                   Alembic schema history
tests/                        API, app, database, deployment, and solver tests
app.py                        Flask configuration and planning workflow
api_routes.py                 Versioned REST API
data_routes.py                Server-rendered data operations
data_service.py               Import and synthetic generation services
database_service.py           Run and allocation transactions
models.py                     SQLAlchemy domain model
report_routes.py              Report pages and downloads
reporting.py                  PDF and XLSX builders
security.py                   Audit and security helpers
```

## Local development

### Requirements

- Python 3.12 or newer
- PowerShell commands below, or their shell equivalents
- No Node.js build step

### Setup

```powershell
git clone https://github.com/divya-aicodes/smart-seating-arrangement.git
Set-Location smart-seating-arrangement

python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

Copy-Item .env.example .env
python -m flask --app app db upgrade
python app.py
```

Open [http://127.0.0.1:5000/](http://127.0.0.1:5000/).

The local default database is SQLite at `instance/smart_seating.db`.

### Environment variables

| Variable | Development default | Production requirement |
| --- | --- | --- |
| `APP_ENV` | `development` | `production` |
| `FLASK_DEBUG` | `false` | `false` |
| `PORT` | `5000` | Platform-provided |
| `DATABASE_URL` | SQLite | Psycopg PostgreSQL URL |
| `SECRET_KEY` | Generated if omitted locally | Explicit secret, minimum 32 characters |
| `SESSION_COOKIE_SECURE` | `false` | `true` |
| `SESSION_COOKIE_SAMESITE` | `Lax` | `Lax`, or `None` only when required |
| `CORS_ALLOWED_ORIGINS` | Empty | Exact HTTPS origins only |
| `CORS_ALLOW_CREDENTIALS` | `false` | Prefer `false` |
| `ALLOWED_HOSTS` | Local hosts | Exact deployment hostnames |
| `TRUST_PROXY_HEADERS` | `false` | Enable only behind a trusted proxy |
| `RUN_MIGRATIONS` | `false` | Platform-specific |

Do not commit a real secret or production database URL.

## Testing

Run the full suite:

```powershell
python -m unittest discover -s tests -v
```

The current suite contains 42 tests covering:

- real and synthetic persistence;
- public routes without login;
- database lineage and transactional allocations;
- CSRF enforcement and exact-origin CORS;
- blocked untrusted write origins;
- security headers and production configuration;
- payload limits, duplicate rejection, and CSV formula-injection prevention;
- API pagination and publication;
- capacity, accessibility, adjacency, and strict failure behavior; and
- handshaking, chromatic-polynomial, Product Rule, and inclusion-exclusion logic.

Compile the Python source:

```powershell
python -m compileall -q .
```

## Vercel deployment

Vercel detects the root Flask application and imports it through
[`api/index.py`](api/index.py). The linked GitHub repository automatically
deploys pushes to `main`.

Configure:

```text
APP_ENV=production
SECRET_KEY=<at least 32 random characters>
DATABASE_URL=postgresql+psycopg://USER:PASSWORD@HOST:5432/DATABASE
ALLOWED_HOSTS=smart-seating-arrangement-tan.vercel.app
SESSION_COOKIE_SECURE=true
SESSION_COOKIE_SAMESITE=Lax
CORS_ALLOWED_ORIGINS=
CORS_ALLOW_CREDENTIALS=false
TRUST_PROXY_HEADERS=false
```

The Vercel build configuration runs:

```powershell
python -m flask --app app db upgrade
```

The PostgreSQL database must be reachable during the build. Vercel's local
filesystem is ephemeral, so SQLite is not suitable for durable production
records, reports, or audit history.

Additional documentation:

- [`DEPLOYMENT.md`](DEPLOYMENT.md) — deployment and operations
- [`DATABASE_SETUP.md`](DATABASE_SETUP.md) — database and migrations
- [`API.md`](API.md) — API contracts and error format

## Current limitations

- The greedy mapper does not guarantee globally minimum subject relaxations.
- A class count below the independence bound does not prove the greedy mapper
  will find a placement.
- Accessibility backtracking can become expensive on adversarial large inputs.
- The full production graph's chromatic polynomial is not calculated.
- Manual runs are not persisted as reportable seating runs.
- Immediate invigilator names are placeholders; complete staff assignment uses
  the persisted invigilator model.
- Durable production operation depends on PostgreSQL, migrations, backups,
  monitoring, and deployment-account controls.

## License

No open-source license file is currently included. Unless the repository owner
adds one, normal copyright restrictions apply.
