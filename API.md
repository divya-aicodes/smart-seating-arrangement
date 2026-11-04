# Smart Seating Arrangement REST API

Phase 6 adds a versioned REST API backed by the same SQL database and services as the
web interface. API responses never use hard-coded student, exam, room, or allocation
records.

## Access and request security

- Base path: `/api/v1`
- Authentication: none; the application intentionally opens directly without sign-in
- Authorization: no account or role gate is applied
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
| GET | `/datasets` | public | List persisted real or synthetic datasets |
| GET | `/datasets/{id}` | public | Dataset details and persisted counts |
| POST | `/datasets/synthetic` | public + CSRF | Generate deterministic synthetic records |
| GET | `/imports/{id}` | public | Read staged-import status, counters, and progress |
| GET | `/imports/{id}/errors` | public | Read paginated row-level validation errors |
| GET | `/jobs/{id}` | public | Read durable queued/running/completed/failed/cancelled job progress |
| GET | `/exam-sessions`, `/exam-sessions/{id}` | public | Read sessions and their exact subject papers |
| POST | `/exam-sessions` | public + CSRF | Create a dated examination session |
| POST | `/exam-sessions/{id}/papers` | public + CSRF | Add one existing subject as a paper in the session |
| POST | `/exam-registrations/bulk` | public + CSRF | Verify exact student-paper registrations atomically |
| GET | `/exams`, `/exams/{id}` | public | Read persisted exam data |
| GET | `/rooms`, `/rooms/{id}` | public | Read persisted room and seat-capacity data |
| GET | `/seating-runs`, `/seating-runs/{id}` | public | Read persisted optimizer runs |
| GET | `/seating-runs/{id}/allocations` | public | Read paginated allocations from valid runs |
| POST | `/seating-runs/{id}/publish` | public + CSRF | Publish a completed valid run |

Because authentication has been removed, persisted student allocation data is available
to anyone who can reach the deployment. Deploy only on a trusted network unless another
access-control layer is placed in front of the application.

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
