# Credo Health FHIR Migration Take-Home

A complete, intentionally small migration flow for synthetic HAPI FHIR R4 data:

```text
HAPI FHIR -> Django management command -> SQLite -> DRF API -> Vue UI
```

The implementation fetches a bounded Patient sample and each Patient's Observations, transforms and upserts them locally, exposes read-only list/detail endpoints, and displays the results in Vue.

## Stack

- Python 3.11, Django 5.2.16, Django REST Framework 3.17.1
- Requests 2.34.2 and SQLite
- Vue 3.5.39, Vite 8.1.4, and Vitest 4.1.10

## Run the application

From the repository root, set up and populate the backend:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r backend/requirements.txt
cd backend
python manage.py migrate
python manage.py migrate_fhir --patient-limit 10
python manage.py runserver
```

The migration source defaults to the public HAPI R4 sandbox and contains synthetic data only. Rerunning the command is safe: records are keyed by `(source_system, fhir_id)` and are inserted, updated, or left unchanged without creating duplicates.

In a second terminal, start the frontend:

```bash
cd frontend
npm ci
npm run dev
```

Open [http://127.0.0.1:5173](http://127.0.0.1:5173). Vite proxies `/api` requests to Django on port 8000.

## API

- `GET /api/patients/` returns Patient summaries.
- `GET /api/patients/{database_id}/` returns one Patient with associated Observations.

The API deliberately excludes decoded raw FHIR resources and Patient contact/identifier fields. Observation results expose both `value_type` and the generic `value`, so non-quantity results remain usable.

## Configuration

Settings can be overridden with environment variables:

| Variable | Default |
|---|---:|
| `FHIR_BASE_URL` | `https://hapi.fhir.org/baseR4` |
| `FHIR_DEFAULT_PATIENT_LIMIT` | `10` |
| `FHIR_PATIENT_PAGE_SIZE` | `100` |
| `FHIR_OBSERVATION_PAGE_SIZE` | `100` |
| `FHIR_CONNECT_TIMEOUT` | `3.05` seconds |
| `FHIR_READ_TIMEOUT` | `20` seconds |
| `FHIR_MAX_RETRIES` | `2` |

`--patient-limit` overrides the default Patient limit for one run.

## Tests and verification

Backend:

```bash
source .venv/bin/activate
cd backend
python manage.py test
python manage.py check
python manage.py makemigrations --check --dry-run
python -m pip check
```

Frontend:

```bash
cd frontend
npm ci
npm test
npm run build
```

Verified on July 14, 2026:

- 60 backend tests passed.
- 2 frontend component tests passed.
- The production frontend build succeeded.
- A live bounded run migrated 2 Patients and 4 Observations with zero rejections.
- Browser verification confirmed the Patient list/detail interaction and no console errors.

Tests use deterministic synthetic fixtures and do not require the live sandbox. Only the explicit `migrate_fhir` command calls HAPI.

## Key decisions and tradeoffs

- The take-home uses bounded FHIR search rather than attempting a 50,000-Patient migration during review. It follows server-provided pagination links and applies explicit timeouts plus bounded retry/backoff for transient failures.
- Each Patient and all fetched Observations are written in one transaction. A bad Patient unit rolls back; previously completed units remain safe and reruns are idempotent.
- Repeating and choice fields are preserved as JSON. Query/display columns are derived without assuming every Observation uses `valueQuantity`.
- A rejected resource fails the take-home run with sanitized aggregate evidence. A durable quarantine is not claimed because it was not required for the working slice.
- SQLite and a synchronous command keep local setup simple. They are not the production choice for a large backfill.
- Authentication, deployment, UI pagination, real-time sync, and visual polish beyond a clear usable screen are intentionally out of scope per the assignment.
- Decoded raw resources are retained locally only because the source is synthetic. Real PHI requires approved encryption, retention, access, audit, and logging controls.

## Exact extension path

1. **Extraction scale:** confirm the real source contract and authentication, then replace per-Patient Observation searches with asynchronous Bulk FHIR `$export` where supported. Stream NDJSON, honor long `Retry-After` values, and persist durable file/page checkpoints.
2. **Data quality:** add source-profile validation and an encrypted quarantine table with sanitized reason codes so one rejected resource need not fail the whole run.
3. **Persistence:** move to PostgreSQL, use bounded batch/native upserts, add run-scoped staging, reconcile source/target counts, and publish or roll back a run atomically.
4. **Incremental sync:** agree on update/delete semantics, then add `_since` or `_lastUpdated` high-watermarks with an overlap window and idempotent replay.
5. **API and UI:** add authentication/authorization first, then server-side API pagination, filters, code/date indexes, and matching UI pagination/filter controls.
6. **Operations:** add CI, container/deployment configuration, structured PHI-safe metrics/logs, alerting, stale-run recovery, secret management, and documented recovery drills.

## AI usage

OpenAI Codex assisted with planning, implementation, tests, review, and documentation. Every generated change was exercised through focused tests, the full suite, or the live synthetic end-to-end check. The candidate should still review and be prepared to explain every decision.

See [Plan.md](Plan.md) for the migration design, field mapping, production safety considerations, and go-live questions.
