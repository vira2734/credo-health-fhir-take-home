# Credo Health FHIR Migration Take-Home

This repository contains the working submission for Credo Health's Full Stack Python Take-Home Exercise.

## Status

Backend milestones 1 and 2 are complete:

- Django and Django REST Framework are scaffolded in `backend/`.
- SQLite is configured for local persistence.
- `Patient`, `Observation`, and `MigrationRun` models and the initial migration are present.
- Focused model tests cover source-key uniqueness, FHIR JSON/choice-field preservation, Patient/Observation integrity, and migration-run defaults.
- A bounded FHIR search client follows opaque pagination links, applies explicit timeouts and bounded retries, and returns sanitized failures.
- Null-safe Patient and Observation transformers preserve repeating and choice fields while deriving only documented query/display projections.
- Focused client and transformer tests cover pagination, retry boundaries, malformed responses, all FHIR R4 Observation value/effective choices, partial dates, false/zero values, and invalid projection inputs.

The ingestion management command, REST endpoints, and Vue frontend have not been implemented yet. The client and transformers are currently exercised through tests only; they do not write source data until the next slice wires them into transactional ingestion.

## Stack

- Python 3.11 (verified with 3.11.9)
- Django 5.2.16 and Django REST Framework 3.17.1
- Requests 2.34.2
- SQLite for the take-home
- Vue 3 and Vite (planned; frontend not started)
- HAPI FHIR R4 synthetic sandbox data (planned ingestion source)

## Backend setup

Run these commands from the repository root:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r backend/requirements.txt
cd backend
python manage.py migrate
python manage.py test
```

The migration creates `backend/db.sqlite3`. The database and virtual environment are ignored by Git.

To start Django's local development server:

```bash
source .venv/bin/activate
cd backend
python manage.py runserver
```

There are no application API routes yet; the current URL scaffold contains only Django's standard admin route.

## Verification

The following commands are expected to pass and were verified for this milestone:

```bash
source .venv/bin/activate
cd backend
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test
```

The current suite contains 39 backend tests. It uses synthetic fixtures and does not call the live HAPI sandbox.

## Current model tradeoffs

- `(source_system, fhir_id)` is unique for Patients and Observations so later ingestion can upsert idempotently.
- Repeating and choice fields remain JSON instead of assuming every Observation has `valueQuantity`.
- `Patient.birth_date` preserves the FHIR date string because valid FHIR dates can have partial precision.
- Patients with Observations are protected from deletion until source deletion behavior is defined.
- The decoded FHIR object is retained only because the take-home source is synthetic. Production PHI would require approved encrypted storage and retention controls.
- SQLite keeps setup small for the exercise; PostgreSQL and batch upserts are the production path.

## Extraction and transformation tradeoffs

- `_count` is treated as a page size, while the configured Patient limit is enforced independently. Server-provided opaque `next` links are followed exactly.
- Pagination links must remain on the configured source origin. Rejecting a directly supplied cross-origin `next` URL reduces accidental forwarding to an unexpected host, but Requests redirects still require a separate policy. Production must review redirect/auth-header behavior and use an explicit host allowlist, including any approved CDN.
- Retries cover transport failures and `429`, `502`, `503`, and `504` only. `Retry-After` and exponential delays are capped so a local command cannot sleep indefinitely; a production scheduler should persist a long server-directed pause rather than retry early.
- The transformer validates resource identity, Patient linkage, choice-field invariants, and fields projected into typed columns. It is not a complete FHIR profile validator; production ingestion should validate agreed profiles and quarantine nonconforming resources.
- FHIR permits leap-second timestamp text, but Python's `datetime` cannot represent it. The take-home returns a sanitized mapping error instead of inventing a nearby timestamp; production must agree on a lossless timestamp strategy.
- Accepted resources retain the decoded synthetic FHIR object, but not original response bytes or decimal lexical formatting such as trailing zeros. The mapper also copies selected arrays/choices for isolation. Those limitations and the small memory cost are acceptable here; production requiring lexical/audit fidelity should land approved raw bytes or parse decimals losslessly and avoid redundant streaming copies.
- Display name and Observation label derivation use deterministic source order for UI convenience only. The complete names and codings remain preserved.

## Next work

1. Add idempotent transactional ingestion through a `migrate_fhir` management command, including meaningful `MigrationRun` counters.
2. Expose the Patient list/detail REST API and add API tests.
3. Build the Vue 3 frontend after the backend slice is complete.
4. Complete end-to-end verification and document the live-sandbox command separately from deterministic tests.

## AI usage

OpenAI Codex assisted with planning, live-source verification, the backend scaffold, model/client/transformer design, tests, review, and documentation. Implementation followed test-driven red/green cycles with independent test review. The submission keeps each decision small and explicit so it can be reviewed and explained by the candidate.

See [Plan.md](Plan.md) for the verified migration design, data mapping, validation, safety, scalability, and rollback approach.
