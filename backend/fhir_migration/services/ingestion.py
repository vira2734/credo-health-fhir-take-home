"""Transactional, idempotent persistence for one bounded FHIR search run."""

from collections.abc import Callable
from datetime import datetime
from typing import Any
from uuid import UUID

from django.core.exceptions import ValidationError
from django.db import DatabaseError, transaction
from django.utils import timezone

from fhir_migration.models import MigrationRun, Observation, Patient
from fhir_migration.services.fhir_client import FhirClient, FhirClientError
from fhir_migration.services.mappers import (
    FhirMappingError,
    MappedObservation,
    map_observation,
    map_patient,
)


class MigrationFailed(RuntimeError):
    """Sanitized failure for a persisted migration run."""

    def __init__(self, run_id: UUID, phase: str) -> None:
        self.run_id = run_id
        self.phase = phase
        super().__init__(f"FHIR migration {run_id} failed during {phase}")


class _RunAbort(RuntimeError):
    def __init__(self, phase: str) -> None:
        self.phase = phase


PROGRESS_FIELDS = (
    "checkpoint",
    "request_count",
    "retry_count",
    "discovered_count",
    "parsed_count",
    "accepted_count",
    "rejected_count",
    "inserted_count",
    "updated_count",
    "unchanged_count",
    "updated_at",
)


def run_fhir_migration(
    *,
    client: FhirClient,
    patient_limit: int,
    now_fn: Callable[[], datetime] = timezone.now,
) -> MigrationRun:
    """Fetch, map, and commit each Patient and its Observations as one unit."""

    if (
        not isinstance(patient_limit, int)
        or isinstance(patient_limit, bool)
        or patient_limit < 1
    ):
        raise ValueError("Patient limit must be a positive integer")

    source_system = client.base_url.rstrip("/")
    starting_stats = client.stats
    run = MigrationRun.objects.create(
        source_system=source_system,
        extraction_mode=MigrationRun.ExtractionMode.SEARCH,
        status=MigrationRun.Status.RUNNING,
        checkpoint={"completed_patient_units": 0},
        started_at=now_fn(),
    )

    try:
        for patient_resource in client.iter_patients(patient_limit):
            run.discovered_count += 1
            mapped_patient = _map_patient(run, patient_resource, source_system)
            mapped_observations = _collect_observations(
                run,
                client,
                mapped_patient["fhir_id"],
                source_system,
            )

            try:
                actions = _persist_patient_unit(
                    mapped_patient,
                    mapped_observations,
                    run,
                    client,
                    starting_stats,
                )
            except ValidationError:
                run.rejected_count += 1
                raise _RunAbort("validation") from None

            run.inserted_count += actions.count("inserted")
            run.updated_count += actions.count("updated")
            run.unchanged_count += actions.count("unchanged")

        _finish_run(
            run,
            status=MigrationRun.Status.SUCCEEDED,
            client=client,
            starting_stats=starting_stats,
            now_fn=now_fn,
        )
        return run
    except _RunAbort as failure:
        _fail_run(run, failure.phase, client, starting_stats, now_fn)
    except FhirClientError:
        _fail_run(run, "extraction", client, starting_stats, now_fn)
    except DatabaseError:
        _fail_run(run, "persistence", client, starting_stats, now_fn)
    except Exception:
        _fail_run(run, "internal", client, starting_stats, now_fn)

    raise AssertionError("Migration failure handler returned unexpectedly")


def _map_patient(
    run: MigrationRun,
    resource: Any,
    source_system: str,
) -> dict[str, Any]:
    try:
        mapped = map_patient(resource, source_system)
    except FhirMappingError:
        run.rejected_count += 1
        raise _RunAbort("mapping") from None
    run.parsed_count += 1
    run.accepted_count += 1
    return mapped


def _collect_observations(
    run: MigrationRun,
    client: FhirClient,
    patient_fhir_id: str,
    source_system: str,
) -> list[MappedObservation]:
    mapped_observations = []
    for resource in client.iter_observations(patient_fhir_id):
        run.discovered_count += 1
        try:
            mapped = map_observation(resource, source_system)
        except FhirMappingError:
            run.rejected_count += 1
            raise _RunAbort("mapping") from None
        run.parsed_count += 1
        if mapped.patient_fhir_id != patient_fhir_id:
            run.rejected_count += 1
            raise _RunAbort("relationship")
        run.accepted_count += 1
        mapped_observations.append(mapped)
    return mapped_observations


def _persist_patient_unit(
    mapped_patient: dict[str, Any],
    mapped_observations: list[MappedObservation],
    run: MigrationRun,
    client: FhirClient,
    starting_stats: Any,
) -> list[str]:
    actions = []
    with transaction.atomic():
        patient, action = _upsert(
            Patient,
            mapped_patient,
            source_system=mapped_patient["source_system"],
            fhir_id=mapped_patient["fhir_id"],
        )
        actions.append(action)

        for mapped in mapped_observations:
            attributes = {**mapped.attributes, "patient": patient}
            _, action = _upsert(
                Observation,
                attributes,
                source_system=attributes["source_system"],
                fhir_id=attributes["fhir_id"],
            )
            actions.append(action)

        completed = run.checkpoint["completed_patient_units"] + 1
        run.checkpoint = {"completed_patient_units": completed}
        _sync_client_stats(run, client, starting_stats)
        run.save(update_fields=PROGRESS_FIELDS)
    return actions


def _upsert(model, attributes: dict[str, Any], **lookup):
    instance = model.objects.select_for_update().filter(**lookup).first()
    if instance is None:
        instance = model(**attributes)
        instance.full_clean()
        instance.save(force_insert=True)
        return instance, "inserted"

    changed_fields = []
    for field, value in attributes.items():
        if getattr(instance, field) != value:
            setattr(instance, field, value)
            changed_fields.append(field)
    instance.full_clean()
    if not changed_fields:
        return instance, "unchanged"

    instance.save(update_fields=[*changed_fields, "updated_at"])
    return instance, "updated"


def _sync_client_stats(run: MigrationRun, client: FhirClient, starting_stats: Any):
    current_stats = client.stats
    run.request_count = current_stats.request_count - starting_stats.request_count
    run.retry_count = current_stats.retry_count - starting_stats.retry_count


def _finish_run(
    run: MigrationRun,
    *,
    status: str,
    client: FhirClient,
    starting_stats: Any,
    now_fn: Callable[[], datetime],
) -> None:
    _sync_client_stats(run, client, starting_stats)
    run.status = status
    run.finished_at = now_fn()
    run.save(update_fields=(*PROGRESS_FIELDS, "status", "finished_at"))


def _fail_run(
    run: MigrationRun,
    phase: str,
    client: FhirClient,
    starting_stats: Any,
    now_fn: Callable[[], datetime],
) -> None:
    try:
        _finish_run(
            run,
            status=MigrationRun.Status.FAILED,
            client=client,
            starting_stats=starting_stats,
            now_fn=now_fn,
        )
    except DatabaseError:
        pass
    raise MigrationFailed(run.id, phase) from None
