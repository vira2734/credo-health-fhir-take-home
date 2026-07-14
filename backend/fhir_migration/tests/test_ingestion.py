from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone

from django.test import TestCase

from fhir_migration.models import MigrationRun, Observation, Patient
from fhir_migration.services.fhir_client import FhirClientError
from fhir_migration.services.ingestion import MigrationFailed, run_fhir_migration


SOURCE_SYSTEM = "https://hapi.fhir.org/baseR4"
NOW = datetime(2024, 3, 4, 5, 6, 7, tzinfo=timezone.utc)
UNCHANGED_AT = datetime(2020, 1, 2, 3, 4, 5, tzinfo=timezone.utc)


@dataclass(frozen=True)
class FakeStats:
    request_count: int
    retry_count: int


class FakeClient:
    def __init__(
        self,
        patients,
        observations_by_patient,
        *,
        base_url=SOURCE_SYSTEM + "/",
        initial_request_count=7,
        initial_retry_count=2,
        retry_patients=(),
        observation_errors=None,
    ):
        self.base_url = base_url
        self._patients = list(patients)
        self._observations_by_patient = observations_by_patient
        self._request_count = initial_request_count
        self._retry_count = initial_retry_count
        self._retry_patients = set(retry_patients)
        self._observation_errors = observation_errors or {}
        self.patient_limits = []

    @property
    def stats(self):
        return FakeStats(self._request_count, self._retry_count)

    def iter_patients(self, limit):
        self.patient_limits.append(limit)
        self._request_count += 1
        yield from self._patients[:limit]

    def iter_observations(self, patient_fhir_id):
        self._request_count += 1
        if patient_fhir_id in self._retry_patients:
            self._request_count += 1
            self._retry_count += 1
        if patient_fhir_id in self._observation_errors:
            raise self._observation_errors[patient_fhir_id]
        yield from self._observations_by_patient.get(patient_fhir_id, [])


def patient_resource(fhir_id, **overrides):
    resource = {
        "resourceType": "Patient",
        "id": fhir_id,
        "name": [{"text": f"Synthetic {fhir_id}"}],
        "active": True,
    }
    resource.update(overrides)
    return resource


def observation_resource(fhir_id, patient_fhir_id, **overrides):
    resource = {
        "resourceType": "Observation",
        "id": fhir_id,
        "subject": {"reference": f"Patient/{patient_fhir_id}"},
        "status": "final",
        "code": {"text": "Synthetic measurement"},
        "valueInteger": 0,
    }
    resource.update(overrides)
    return resource


class FhirIngestionTests(TestCase):
    def run_migration(self, client, *, patient_limit=10):
        return run_fhir_migration(
            client=client,
            patient_limit=patient_limit,
            now_fn=lambda: NOW,
        )

    def test_successfully_inserts_patient_and_observations_and_records_run(self):
        patient = patient_resource("patient-1")
        observations = [
            observation_resource("observation-1", "patient-1"),
            observation_resource(
                "observation-2",
                "patient-1",
                valueBoolean=False,
                valueInteger=None,
            ),
        ]
        observations[1].pop("valueInteger")
        client = FakeClient(
            [patient],
            {"patient-1": observations},
            retry_patients={"patient-1"},
        )

        run = self.run_migration(client, patient_limit=1)

        self.assertEqual(client.patient_limits, [1])
        self.assertEqual(run.status, MigrationRun.Status.SUCCEEDED)
        self.assertEqual(run.extraction_mode, MigrationRun.ExtractionMode.SEARCH)
        self.assertEqual(run.source_system, SOURCE_SYSTEM)
        self.assertEqual(run.started_at, NOW)
        self.assertEqual(run.finished_at, NOW)
        self.assertEqual(run.checkpoint, {"completed_patient_units": 1})
        self.assertEqual(run.request_count, 3)
        self.assertEqual(run.retry_count, 1)
        self.assertEqual(run.discovered_count, 3)
        self.assertEqual(run.parsed_count, 3)
        self.assertEqual(run.accepted_count, 3)
        self.assertEqual(run.rejected_count, 0)
        self.assertEqual(run.inserted_count, 3)
        self.assertEqual(run.updated_count, 0)
        self.assertEqual(run.unchanged_count, 0)

        saved_patient = Patient.objects.get()
        self.assertEqual(saved_patient.source_system, SOURCE_SYSTEM)
        self.assertEqual(saved_patient.fhir_id, "patient-1")
        self.assertEqual(saved_patient.display_name, "Synthetic patient-1")
        self.assertEqual(saved_patient.raw_resource, patient)
        saved_observations = list(
            saved_patient.observations.order_by("fhir_id")
        )
        self.assertEqual(len(saved_observations), 2)
        self.assertEqual(saved_observations[0].value_type, "valueInteger")
        self.assertEqual(saved_observations[0].value_numeric, 0)
        self.assertEqual(saved_observations[1].value_type, "valueBoolean")
        self.assertIs(saved_observations[1].value, False)

    def test_identical_rerun_is_unchanged_without_rewriting_rows(self):
        patient = patient_resource("patient-1")
        observations = [
            observation_resource("observation-1", "patient-1"),
            observation_resource("observation-2", "patient-1"),
        ]
        self.run_migration(
            FakeClient([patient], {"patient-1": observations})
        )
        Patient.objects.update(updated_at=UNCHANGED_AT)
        Observation.objects.update(updated_at=UNCHANGED_AT)

        rerun = self.run_migration(
            FakeClient([patient], {"patient-1": observations})
        )

        self.assertEqual(Patient.objects.count(), 1)
        self.assertEqual(Observation.objects.count(), 2)
        self.assertEqual(rerun.inserted_count, 0)
        self.assertEqual(rerun.updated_count, 0)
        self.assertEqual(rerun.unchanged_count, 3)
        self.assertEqual(Patient.objects.get().updated_at, UNCHANGED_AT)
        self.assertEqual(
            set(Observation.objects.values_list("updated_at", flat=True)),
            {UNCHANGED_AT},
        )

    def test_changed_resource_updates_without_creating_a_duplicate(self):
        patient = patient_resource("patient-1")
        observations = [
            observation_resource("observation-1", "patient-1"),
            observation_resource("observation-2", "patient-1"),
        ]
        self.run_migration(
            FakeClient([patient], {"patient-1": observations})
        )
        changed_patient = deepcopy(patient)
        changed_patient["active"] = False

        rerun = self.run_migration(
            FakeClient([changed_patient], {"patient-1": observations})
        )

        self.assertEqual(Patient.objects.count(), 1)
        self.assertEqual(Observation.objects.count(), 2)
        self.assertIs(Patient.objects.get().active, False)
        self.assertEqual(rerun.inserted_count, 0)
        self.assertEqual(rerun.updated_count, 1)
        self.assertEqual(rerun.unchanged_count, 2)

    def test_rerun_does_not_infer_deletion_from_an_absent_observation(self):
        patient = patient_resource("patient-1")
        observations = [
            observation_resource("observation-1", "patient-1"),
            observation_resource("observation-2", "patient-1"),
        ]
        self.run_migration(
            FakeClient([patient], {"patient-1": observations})
        )

        rerun = self.run_migration(
            FakeClient([patient], {"patient-1": observations[:1]})
        )

        self.assertEqual(Observation.objects.count(), 2)
        self.assertEqual(rerun.unchanged_count, 2)
        self.assertEqual(rerun.updated_count, 0)

    def test_observation_for_a_different_patient_fails_the_whole_unit(self):
        client = FakeClient(
            [patient_resource("patient-1")],
            {
                "patient-1": [
                    observation_resource("observation-1", "patient-2")
                ]
            },
        )

        with self.assertRaises(MigrationFailed) as raised:
            self.run_migration(client)

        run = MigrationRun.objects.get(pk=raised.exception.run_id)
        self.assertEqual(raised.exception.phase, "relationship")
        self.assertEqual(run.status, MigrationRun.Status.FAILED)
        self.assertEqual(run.finished_at, NOW)
        self.assertEqual(run.checkpoint, {"completed_patient_units": 0})
        self.assertEqual(run.discovered_count, 2)
        self.assertEqual(run.parsed_count, 2)
        self.assertEqual(run.accepted_count, 1)
        self.assertEqual(run.rejected_count, 1)
        self.assertEqual(run.inserted_count, 0)
        self.assertEqual(run.updated_count, 0)
        self.assertEqual(run.unchanged_count, 0)
        self.assertEqual(Patient.objects.count(), 0)
        self.assertEqual(Observation.objects.count(), 0)

    def test_model_validation_failure_rolls_back_patient_and_observations(self):
        client = FakeClient(
            [patient_resource("patient-1")],
            {
                "patient-1": [
                    observation_resource(
                        "observation-1",
                        "patient-1",
                        status="x" * 33,
                    )
                ]
            },
        )

        with self.assertRaises(MigrationFailed) as raised:
            self.run_migration(client)

        run = MigrationRun.objects.get(pk=raised.exception.run_id)
        self.assertEqual(raised.exception.phase, "validation")
        self.assertEqual(run.status, MigrationRun.Status.FAILED)
        self.assertEqual(run.discovered_count, 2)
        self.assertEqual(run.parsed_count, 2)
        self.assertEqual(run.accepted_count, 1)
        self.assertEqual(run.rejected_count, 1)
        self.assertEqual(run.inserted_count, 0)
        self.assertEqual(run.updated_count, 0)
        self.assertEqual(run.unchanged_count, 0)
        self.assertEqual(Patient.objects.count(), 0)
        self.assertEqual(Observation.objects.count(), 0)

    def test_failure_preserves_a_previously_completed_patient_unit(self):
        patients = [patient_resource("patient-1"), patient_resource("patient-2")]
        observations = {
            "patient-1": [observation_resource("observation-1", "patient-1")],
            "patient-2": [
                observation_resource(
                    "observation-2",
                    "patient-2",
                    status="x" * 33,
                )
            ],
        }

        with self.assertRaises(MigrationFailed) as raised:
            self.run_migration(FakeClient(patients, observations))

        run = MigrationRun.objects.get(pk=raised.exception.run_id)
        self.assertEqual(run.status, MigrationRun.Status.FAILED)
        self.assertEqual(run.checkpoint, {"completed_patient_units": 1})
        self.assertEqual(run.request_count, 3)
        self.assertEqual(run.discovered_count, 4)
        self.assertEqual(run.parsed_count, 4)
        self.assertEqual(run.accepted_count, 3)
        self.assertEqual(run.rejected_count, 1)
        self.assertEqual(run.inserted_count, 2)
        self.assertEqual(run.updated_count, 0)
        self.assertEqual(run.unchanged_count, 0)
        self.assertEqual(
            list(Patient.objects.values_list("fhir_id", flat=True)),
            ["patient-1"],
        )
        self.assertEqual(
            list(Observation.objects.values_list("fhir_id", flat=True)),
            ["observation-1"],
        )

    def test_source_failure_does_not_persist_patient_or_sensitive_details(self):
        sensitive_marker = "synthetic-sensitive-clinical-marker"
        client = FakeClient(
            [patient_resource("patient-1")],
            {},
            observation_errors={
                "patient-1": FhirClientError(sensitive_marker)
            },
        )

        with self.assertRaises(MigrationFailed) as raised:
            self.run_migration(client)

        run = MigrationRun.objects.get(pk=raised.exception.run_id)
        self.assertEqual(raised.exception.phase, "extraction")
        self.assertNotIn(sensitive_marker, str(raised.exception))
        self.assertNotIn(sensitive_marker, str(run.checkpoint))
        self.assertEqual(run.status, MigrationRun.Status.FAILED)
        self.assertEqual(run.request_count, 2)
        self.assertEqual(run.discovered_count, 1)
        self.assertEqual(run.parsed_count, 1)
        self.assertEqual(run.accepted_count, 1)
        self.assertEqual(run.rejected_count, 0)
        self.assertEqual(run.inserted_count, 0)
        self.assertEqual(Patient.objects.count(), 0)
        self.assertEqual(Observation.objects.count(), 0)

    def test_invalid_patient_limit_creates_no_migration_run(self):
        client = FakeClient([], {})

        for patient_limit in (0, -1, True, 1.5, "1"):
            with self.subTest(patient_limit=patient_limit):
                with self.assertRaises(ValueError):
                    self.run_migration(client, patient_limit=patient_limit)

        self.assertEqual(MigrationRun.objects.count(), 0)
