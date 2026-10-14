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
| GET | `/exams`, `/exams/{id}` | public | Read persisted exam data |
| GET | `/rooms`, `/rooms/{id}` | public | Read persisted room and seat-capacity data |
| GET | `/seating-runs`, `/seating-runs/{id}` | public | Read persisted optimizer runs |
| GET | `/seating-runs/{id}/allocations` | public | Read paginated allocations from valid runs |
| POST | `/seating-runs/{id}/publish` | public + CSRF | Publish a completed valid run |

Because authentication has been removed, persisted student allocation data is available
to anyone who can reach the deployment. Deploy only on a trusted network unless another
access-control layer is placed in front of the application.

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
