# Credo Health FHIR Migration Take-Home

This repository contains the working submission for Credo Health's Full Stack Python Take-Home Exercise.

## Status

Backend milestone 1 is complete:

- Django and Django REST Framework are scaffolded in `backend/`.
- SQLite is configured for local persistence.
- `Patient`, `Observation`, and `MigrationRun` models and the initial migration are present.
- Focused model tests cover source-key uniqueness, FHIR JSON/choice-field preservation, Patient/Observation integrity, and migration-run defaults.

FHIR extraction, transformation, the management command, REST endpoints, and the Vue frontend have not been implemented yet.

## Stack

- Python 3.11 (verified with 3.11.9)
- Django 5.2.16 and Django REST Framework 3.17.1
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

## Current model tradeoffs

- `(source_system, fhir_id)` is unique for Patients and Observations so later ingestion can upsert idempotently.
- Repeating and choice fields remain JSON instead of assuming every Observation has `valueQuantity`.
- `Patient.birth_date` preserves the FHIR date string because valid FHIR dates can have partial precision.
- Patients with Observations are protected from deletion until source deletion behavior is defined.
- Raw FHIR JSON is retained only because the take-home source is synthetic. Production PHI would require approved encrypted storage and retention controls.
- SQLite keeps setup small for the exercise; PostgreSQL and batch upserts are the production path.

## Next work

1. Add the bounded, paginated FHIR client and null-safe resource transformers.
2. Add idempotent transactional ingestion through a `migrate_fhir` management command.
3. Expose the Patient list/detail REST API and add API/client tests.
4. Build the Vue 3 frontend after the backend slice is complete.

## AI usage

OpenAI Codex assisted with the backend scaffold, model design, tests, and documentation. The submission keeps the implementation small and explicit so each decision can be reviewed and explained by the candidate.

See [Plan.md](Plan.md) for the verified migration design, data mapping, validation, safety, scalability, and rollback approach.
