# FHIR R4 Patient and Observation Migration Plan

> Status: the complete take-home flow was verified on 2026-07-14 PT: bounded FHIR extraction, transformation, transactional SQLite upserts, the Patient list/detail API, and the Vue list/detail UI. Verification includes 60 backend tests, 2 frontend tests, a production frontend build, and a live synthetic run of 2 Patients with 4 Observations. Unknown production facts remain go-live gates rather than assumptions.

## 1. Migration steps

1. **Discover and freeze scope.** Read the source `CapabilityStatement`, confirm the Patient cohort, eligible Observation types, profiles, authentication, rate limits, deletion behavior, and an extraction cutoff time.
2. **Extract.** For the take-home, fetch a bounded Patient sample and each patient's Observations as FHIR JSON. For a production backfill, prefer asynchronous Bulk FHIR `$export` when the source confirms it is supported.
3. **Land and checkpoint.** Record a `MigrationRun`, retain the source resource ID/version metadata, and checkpoint completed pages/files so a stopped run can resume safely.
4. **Transform.** Validate `resourceType`, map the FHIR resources into the internal schema below, and quarantine malformed or out-of-scope records with a reason.
5. **Load idempotently.** Upsert by `(source_system, fhir_id)` in bounded transactions; load Patients before Observations and enforce the foreign key.
6. **Reconcile.** Compare extracted, accepted, rejected, and persisted counts; check duplicates, relationships, mapped values, and a deterministic sample against the source.
7. **Publish.** Promote only a fully validated run. Keep the previous published dataset available until acceptance is complete.
8. **Operate or roll back.** Monitor progress and retry only transient failures. Resume from a checkpoint, discard an unpromoted run, or switch back to the prior dataset if validation fails.

```mermaid
flowchart LR
    A["FHIR R4 source"] --> B["Extract: Bulk export or paged search"]
    B --> C["Versioned staging + checkpoints"]
    C --> D["Validate and transform"]
    D -->|valid| E["Idempotent Patient/Observation upserts"]
    D -->|invalid| Q["Quarantine + reason"]
    E --> F["Reconcile counts and samples"]
    F -->|pass| G["Atomic publish"]
    F -->|fail| R["Discard run / retain prior version"]
```

## 2. Verified source behavior and decisions

The assignment specifies a synthetic-data-only migration of about 50,000 Patients and their Observations from FHIR R4, plus a Django/Python or equivalent backend, local persistence, a read REST API, a Vue or equivalent UI, deliberate external-API failure handling, meaningful backend tests, a README, and AI-use disclosure. Authentication, deployment, visual polish, real-time sync, and UI pagination are explicitly out of scope for the working slice.

The live sandbox `https://hapi.fhir.org/baseR4` currently advertises FHIR `4.0.1`, JSON and XML, Patient and Observation `read`/`search-type`, Observation `subject` and `patient` search parameters, `_lastUpdated`, and system `$export`. Live checks returned searchset Bundles with opaque `link[relation="next"].url` values. A real `$export?_type=Patient,Observation` kickoff returned `202 Accepted` and a polling URL; polling returned `202` with `Retry-After: 120`. At verification time the volatile sandbox reported 46,449 Patients and 132,466 Observations; these are evidence that the dataset is substantial, not acceptance counts, because the public server is periodically reset.

**Working-slice decision:** use `Accept: application/fhir+json`, Django + Django REST Framework, SQLite, Vue 3, and `python manage.py migrate_fhir --patient-limit 10`. Fetch Patients from `/Patient?_count=<limit>` and Observations from `/Observation?subject=Patient/{id}&_count=100`, following any server-provided `next` URL. Expose `GET /api/patients/` and `GET /api/patients/{id}/` (including Observations); the Vue screen lists migrated Patients and opens the selected Patient's results. The source URL and limits remain configuration.

**Production decision:** use system-level Bulk Data export for the initial backfill because it avoids roughly one Observation request per Patient and returns streamable NDJSON files. Poll exactly as directed by `Retry-After`, stream files instead of loading them into memory, verify the manifest, and bound concurrent downloads/workers. If Bulk Data is unavailable, page through Patient and Observation searches separately and always follow the exact server-provided `next` URL. `_revinclude=Observation:subject` is supported by this sandbox, but would be adopted only after source-specific volume testing. For later deltas, use Bulk `_since` or `_lastUpdated` with a recorded high-watermark, a small overlap window, and idempotent upserts. Page size, concurrency, and requests/second are configuration values agreed with the source owner; the current CapabilityStatement publishes no numeric rate limit.

Every request has connect/read timeouts. Retry idempotent GET/status/download calls only for transport errors, `429`, `502`, `503`, and `504`, honoring `Retry-After`; otherwise use capped exponential backoff with jitter. Retries are bounded, permanent `4xx` responses fail deliberately, and FHIR `OperationOutcome` details are sanitized before logging. A circuit breaker pauses extraction after sustained source failure.

The implemented take-home client enforces the Patient limit independently from `_count`, accepts search Bundles without `total`, skips warning `OperationOutcome` entries, detects pagination loops, and follows opaque absolute `next` links without reconstructing them. It rejects directly supplied `next` links outside the configured origin, reducing accidental cross-origin forwarding. This is not complete redirect or SSRF protection because Requests follows HTTP redirects by default; production must review redirect and authentication-header behavior and use an explicit allowlist for every approved host. To keep the synchronous local command bounded, even `Retry-After` is capped; production orchestration should persist and honor longer pauses instead of retrying earlier than the source requested.

## 3. Internal mapping

FHIR fields are optional and repeating, so transformers must be null-safe and must not silently choose clinical meaning. The take-home keeps the decoded FHIR object in `raw_resource` for traceability because all data is synthetic. It does not retain original response bytes, and normal JSON decoding can lose decimal lexical details such as trailing zeros. Production requiring lexical or audit fidelity must land approved raw bytes or use lossless decimal parsing; any raw-resource retention requires an approved encrypted store and retention period.

The implemented transformer validates resource identity, exact relative Patient linkage, choice invariants, FHIR date precision, and values projected into typed columns. It deliberately does not perform full profile validation; that requires source-specific profiles and is a production go-live gate. Invalid or unrepresentable projected timestamps produce sanitized mapping errors. In particular, FHIR's lexical format permits leap seconds while Python's `datetime` does not, so this working slice rejects them rather than normalizing to an invented instant. Accepted resources preserve their decoded synthetic FHIR object.

| Internal entity | FHIR source | Mapping rule |
|---|---|---|
| `Patient` identity | `Patient.id`, `meta.versionId`, `meta.lastUpdated` | Store as `fhir_id` plus source metadata; `Patient.id` is not treated as an MRN. Unique on `(source_system, fhir_id)`. |
| Patient display | `name[]`, `gender`, `birthDate`, `active` | Preserve names as JSON; derive `display_name` deterministically for UI only. Store administrative gender as supplied and nullable birth date/active. |
| Patient contact | `identifier[]`, `telecom[]`, `address[]`, `communication[]` | Preserve normalized arrays. Do not select a canonical identifier, phone, or address until a business rule is agreed. |
| `Observation` identity/link | `Observation.id`, `subject.reference` | Unique on `(source_system, fhir_id)`; resolve only `Patient/{id}` subjects to the Patient foreign key. The take-home fails the run on unresolved or out-of-cohort references; production adds quarantine. |
| Observation meaning | `status`, `category[]`, `code.coding[]`, `code.text` | Preserve all categories/codings; derive a display label without discarding coding system and code (for example LOINC). |
| Observation result | `value[x]`, `dataAbsentReason`, `component[]`, `referenceRange[]` | Store `value_type` and typed JSON value, with optional numeric/text/unit columns for querying. Do not assume `valueQuantity`; FHIR R4 permits quantity, concept, string, boolean, integer, range, ratio, sampled data, time, date-time, and period. Preserve components and absent reasons. |
| Observation time | `effective[x]`, `issued`, `meta.lastUpdated` | Preserve every effective type/value; populate `effective_at` for a complete timezone-bearing `effectiveDateTime` or representable `effectiveInstant`. Partial date-times remain preserved without a derived timestamp. Keep issued and source update times separate. |

Production uses PostgreSQL, batch upserts, and indexes on Patient source ID plus Observation `(patient_id, effective_at)`, code, and source update time. At-least-once processing plus unique constraints is preferred to a fragile claim of exactly-once delivery.

## 4. Validation, observability, safety, and rollback

Each `MigrationRun` records extraction mode, status, a completed-Patient-unit checkpoint, request/retry counts, and totals for discovered, parsed, accepted, rejected, inserted, updated, and unchanged records. The checkpoint supports progress evidence and safe idempotent restart from the beginning; it is not presented as durable opaque-page resume. Production structured logs and metrics include `run_id`, resource type, latency, retries, rate-limit responses, queue depth, and sanitized error codes, never names, birth dates, identifiers, clinical values, raw bodies, or signed download URLs.

A successful take-home run requires every discovered resource to be accepted and classified as inserted, updated, or unchanged, with no duplicate source keys or orphan Observations. A rejected resource marks the run failed; the current Patient unit rolls back while earlier completed units remain, and an idempotent rerun is safe. Production acceptance additionally requires durable quarantine/reason accounting and reproducible source-to-target sampling. Transformer tests cover missing names, no value, non-quantity values, components, and relationship errors; client tests cover pagination, retry then success, permanent failure, and malformed FHIR. API tests cover `GET /api/patients/` and `GET /api/patients/{id}/` with nested Observations.

The repository uses only the sandbox's synthetic records. In a real migration, PHI must stay out of source control, fixtures, screenshots, analytics, exception trackers, and logs. Use least-privilege service credentials, TLS in transit, encryption at rest and for temporary files, managed secrets, restricted networks, audited access, approved retention/deletion, and a vendor/BAA review as applicable. These production controls follow the HIPAA Security Rule's access-control, audit, integrity, authentication, and transmission-security safeguards; authentication remains out of scope only for this exercise.

Production writes to a run-scoped staging version. Validation failure leaves the current version untouched and deletes or retains the failed version according to policy. A stopped run resumes at the last completed file/page; replay is safe because writes are idempotent. After promotion, rollback atomically restores the previous version, records the reason and affected run, and retains reconciliation evidence. In the take-home, each Patient and its Observations are written in one database transaction; a failed unit rolls back, is reported, and can be retried without duplication.

## 5. Production go-live gates

Before a real run, the source and product owners must confirm: the exact Patient cohort; eligible Observation categories/codes/statuses; source profiles/extensions; identifier and contact precedence; auth/token lifetime; numeric rate/concurrency limits; Bulk export support and file expiry; update/delete/history semantics; cutoff and downtime expectations; PHI retention; reconciliation thresholds; rollback owner; and final sign-off. None of these unspecified values is inferred from the public sandbox.

## References

- [Credo Health take-home instructions](https://credo-health.fibery.io/Hiring/Assessments/Full-Stack-Python-Take-Home-Exercise-1?sharing-key=f442622a-cb82-46b5-af49-350e0e2d3837)
- [Live HAPI FHIR R4 CapabilityStatement](https://hapi.fhir.org/baseR4/metadata)
- [FHIR R4 Patient](https://hl7.org/fhir/R4/patient.html), [Observation](https://hl7.org/fhir/R4/observation.html), [Search](https://hl7.org/fhir/R4/search.html), and [Bundle](https://hl7.org/fhir/R4/bundle.html)
- [HL7 Bulk Data Access: Export](https://build.fhir.org/ig/HL7/bulk-data/export.html)
- [HHS HIPAA Security Rule summary](https://www.hhs.gov/hipaa/for-professionals/security/laws-regulations/index.html)

## Decision log

| Decision | Reason / revisit trigger |
|---|---|
| JSON for the implementation | Verified server support; direct Python/JavaScript handling; FHIR-native content type. Revisit only if the real source contract requires XML. |
| Bounded search for the working slice; Bulk export for production backfill | Matches the assessment's time box while showing a scalable path. Revisit after real source capability and load tests. |
| Preserve repeating/choice fields and decoded synthetic resources | Avoids silent loss while providing queryable simplified fields. Original response bytes and decimal lexical formatting are not retained; revisit production retention after PHI/security review. |
| Django 5.2.16 and Django REST Framework 3.17.1 | Reproducible supported dependencies for the locally available Python 3.11 runtime. Upgrade deliberately after running the full test suite. |
| Preserve `Patient.birth_date` as a nullable FHIR date string | FHIR dates may have year or year-month precision, which a Django `DateField` cannot represent without inventing a day. The transformer validates the retained source syntax. |
| Protect Patients that have Observations | `PROTECT` avoids silently deleting dependent clinical records before source deletion semantics are agreed. The ingestion layer must invoke model validation so Patient and Observation source systems cannot diverge. |
| Follow only same-origin opaque search pagination links | Preserves server paging state while reducing direct cross-origin forwarding risk. Production must separately restrict redirects, review authentication-header behavior, and use an explicit host allowlist for any CDN or file host. |
| Validate projections rather than every FHIR profile rule | Keeps the take-home bounded without pretending generic parsing proves clinical conformance. Add agreed profile validation and quarantine before production. |
| Reject leap-second projections in the take-home | Python `datetime` cannot represent FHIR's permitted `:60`; rejecting with a typed sanitized error avoids inventing time. Revisit with a lossless source-time representation in production. |
| Copy selected JSON values as well as the raw resource | Prevents caller mutation in this small bounded flow. Production streaming should reduce redundant copies to control memory. |
| Keep the API and Vue UI read-only and unpaginated | Matches the assignment's bounded local slice. Add authorization before server-side pagination, filtering, and broader field exposure. |
