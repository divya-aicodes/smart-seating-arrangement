# SeatPlan performance benchmarks

Import and optimization measurements are deliberately separate. These scripts are not
part of ordinary CI and do not turn configured row limits into performance claims.

## PostgreSQL import suite

Use a new, disposable database. The benchmark runs the production CSV staging path,
including Psycopg `COPY`, then the real transactional commit path. It intentionally
retains its generated records so counts remain independently inspectable.

```powershell
$env:BENCHMARK_DATABASE_URL = "postgresql+psycopg://USER:PASSWORD@HOST:5432/seatplan_benchmark"
$env:BENCHMARK_ALLOW_WRITES = "1"
$env:DATABASE_URL = $env:BENCHMARK_DATABASE_URL
python -m flask --app app db upgrade
python -m benchmarks.import_benchmark --sizes 1000,10000
```

Allowed targets are 1K, 10K, 50K, 100K, 250K, and 500K rows. Run the larger targets
only on representative infrastructure. Each target uses a new dataset, so use a fresh
database for repeatable comparisons.

## Optimization suite

```powershell
python -m benchmarks.optimization_benchmark --sizes 100,500,1000
```

The suite records feasibility, seat-graph size, greedy time, bounded CP-SAT status and
time, independent-validation time, hard violations, soft relaxations, and Python peak
allocated memory. CP-SAT is expected to report `SKIPPED_BOUND` above its configured
model bounds; that is not an error and is not an optimality claim.

## Claim gate

```powershell
python -m benchmarks.verify_claims benchmark-results/import-*.json
```

The command succeeds only when both exact 100K and 500K measurements used PostgreSQL,
completed staging and commit, retained every requested row, and reported zero invalid
rows. Timing on a developer laptop or GitHub runner describes only that environment.
It is not automatically evidence for production capacity or request-time limits.

The manual GitHub Actions workflow can run one exact target and uploads its JSON as a
30-day artifact. Its PostgreSQL service is disposable. Expensive targets never run on
push or pull request.

`python_peak_allocated_mib` is measured by Python's `tracemalloc`; it is not process RSS
and excludes memory owned only by native libraries and PostgreSQL.
