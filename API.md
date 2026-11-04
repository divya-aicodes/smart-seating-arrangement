# Smart Seating Arrangement REST API

Phase 6 adds a versioned REST API backed by the same SQL database and services as the
web interface. API responses never use hard-coded student, exam, room, or allocation
records.

## Access and request security

- Base path: `/api/v1`
- Authentication: Flask-Login session cookie; only discovery, health, and OpenAPI are public
- Authorization: viewer reads, planner operational writes, administrator publication and administration
- CSRF: every state-changing request requires the session CSRF token in the
  `X-CSRF-Token` header
- CORS: browser origins are denied unless they exactly match the application origin or
  an entry in `CORS_ALLOWED_ORIGINS`; wildcard origins are not used
- Pagination: collection endpoints accept `page` and `per_page`; `per_page` is capped at
  100

The API index is available at `/api/v1`, its machine-readable contract at
`/api/v1/openapi.json`, and database readiness at `/api/v1/health`.

## Resources

| Method | Endpoint | Access | Purpose |
|---|---|---|---|
| GET | `/datasets`, `/datasets/{id}` | authenticated | Institution-scoped dataset records |
| POST | `/datasets/synthetic` | planner/admin + CSRF | Generate deterministic synthetic records |
| GET | `/imports/{id}`, `/imports/{id}/errors` | authenticated | Institution-scoped import progress and errors |
| GET | `/jobs`, `/jobs/{id}` | authenticated | Institution-scoped background-job progress |
| GET | `/exam-sessions`, `/exam-sessions/{id}` | authenticated | Read sessions and exact papers |
| POST | `/exam-sessions`, `/exam-sessions/{id}/papers` | planner/admin + CSRF | Manage sessions and papers |
| POST | `/exam-registrations/bulk` | planner/admin + CSRF | Verify exact registrations atomically |
| GET | `/exams`, `/exams/{id}` | authenticated | Read persisted exam data |
| GET | `/rooms`, `/rooms/{id}` | authenticated | Read room and capacity data |
| GET | `/seating-runs`, `/seating-runs/{id}/allocations` | authenticated | Read optimizer results |
| POST | `/seating-runs/{id}/publish` | admin + CSRF | Publish a completed valid run |

Successful response bodies and endpoint paths remain backward compatible. Anonymous
record requests now return the standard JSON error envelope with HTTP 401. Tenant
filters return HTTP 404 for another institution's identifiers rather than revealing
that the record exists.

## Sessions, papers, and registration conflicts

An examination session defines when a shift occurs. An exam is one subject paper inside
that session. The existing `/exams` response keeps its scheduling fields for backward
compatibility and adds `exam_session_id`; the session is now the scheduling source of
truth.

Create a session:

```json
{
  "dataset_id": 12,
  "exam_date": "2026-12-02",
  "start_time": "09:00",
  "end_time": "12:00",
  "session_name": "Morning",
  "academic_year": "2026-27"
}
```

Add a paper with `POST /api/v1/exam-sessions/{id}/papers`:

```json
{"subject_id": 41}
```

Verify registrations with `POST /api/v1/exam-registrations/bulk`:

```json
{
  "verification_source": "controller_of_examinations",
  "registrations": [
    {"student_id": 501, "exam_id": 73},
    {"student_id": 502, "exam_id": 74}
  ]
}
```

Every item names an exact paper; the API never infers a paper from department or
specialisation. The complete batch is rolled back if a student is duplicated within a
session, already has another paper in that session, references inactive or mismatched
data, or has another verified registration whose time range overlaps on the same date.

## Import status

`GET /api/v1/imports/{id}` returns the durable state of one staged import. Important
fields include `status`, `total_rows`, `processed_rows`, `valid_rows`, `invalid_rows`,
`progress_percentage`, `checksum_sha256`, `chunk_size`, and timestamps. Expected status
values are `pending`, `processing`, `validated`, `committing`, `completed`, `failed`,
and `cancelled`.

`GET /api/v1/imports/{id}/errors?page=1&per_page=50` returns the standard paginated
collection shape. Each item contains `row_number`, `error_code`, `message`, and bounded
`raw_data`. The browser workflow also offers the same diagnostics as a formula-safe CSV
download. Import creation and confirmation currently remain CSRF-protected web actions;
the versioned write endpoints are planned for the API-security phase.

## Background-job status

`GET /api/v1/jobs?page=1&per_page=25&type=report_generation&status=running`
returns a paginated job collection. `type` and `status` are allowlisted; task payloads
and private artifact keys are excluded.

`GET /api/v1/jobs/{id}` reads progress from PostgreSQL rather than from Redis. The
response includes job type, state, current and total work, percentage, attempts,
safe-cancellation state, timestamps, a bounded error message, and the result URL after
completion. Private storage object keys and task payloads are never returned.

Queued work uses `queued`, `running`, `completed`, `failed`, and `cancelled`. Retry and
cancellation remain CSRF-protected browser actions. Redis is a transport and is not the
permanent source of job history.

## Synthetic dataset request

```json
{
  "dataset_name": "Semester 1 Capacity Test",
  "dataset_version": "v1",
  "seed": 42,
  "student_count": 60,
  "class_count": 3,
  "subject_count": 3,
  "room_count": 2,
  "rows_per_room": 5,
  "benches_per_row": 3,
  "seats_per_bench": 2,
  "exam_date": "2026-12-02"
}
```

The seed makes generation reproducible, while the records themselves are written to the
normal `datasets`, `students`, `subjects`, `exams`, `registrations`, `rooms`, `seats`, and
`invigilators` tables.
