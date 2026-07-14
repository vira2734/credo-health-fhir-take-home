import uuid

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models.deletion import ProtectedError
from django.test import TestCase

from fhir_migration.models import MigrationRun, Observation, Patient

SOURCE_SYSTEM = "https://hapi.fhir.org/baseR4"


class PatientModelTests(TestCase):
    def test_source_system_and_fhir_id_are_unique_together(self):
        Patient.objects.create(source_system=SOURCE_SYSTEM, fhir_id="patient-1")

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Patient.objects.create(
                    source_system=SOURCE_SYSTEM,
                    fhir_id="patient-1",
                )

        Patient.objects.create(
            source_system="https://synthetic.example/fhir",
            fhir_id="patient-1",
        )
        self.assertEqual(Patient.objects.count(), 2)

    def test_json_defaults_are_not_shared_between_patients(self):
        first = Patient(source_system=SOURCE_SYSTEM, fhir_id="patient-1")
        second = Patient(source_system=SOURCE_SYSTEM, fhir_id="patient-2")

        first.names.append({"family": "Synthetic"})
        first.identifiers.append({"system": "urn:synthetic", "value": "one"})
        first.raw_resource["resourceType"] = "Patient"

        self.assertEqual(second.names, [])
        self.assertEqual(second.identifiers, [])
        self.assertEqual(second.raw_resource, {})

    def test_partial_fhir_birth_date_precision_is_preserved(self):
        patient = Patient(
            source_system=SOURCE_SYSTEM,
            fhir_id="patient-partial-date",
            birth_date="1980-04",
            active=None,
        )

        patient.full_clean()
        patient.save()
        patient.refresh_from_db()

        self.assertEqual(patient.birth_date, "1980-04")
        self.assertIsNone(patient.active)

    def test_string_representation_uses_source_identity_not_display_name(self):
        patient = Patient(
            source_system=SOURCE_SYSTEM,
            fhir_id="patient-1",
            display_name="Synthetic Person",
        )

        self.assertEqual(str(patient), f"{SOURCE_SYSTEM}:patient-1")


class ObservationModelTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.patient = Patient.objects.create(
            source_system=SOURCE_SYSTEM,
            fhir_id="patient-1",
        )

    def test_source_system_and_fhir_id_are_unique_together(self):
        Observation.objects.create(
            patient=self.patient,
            source_system=SOURCE_SYSTEM,
            fhir_id="observation-1",
        )

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Observation.objects.create(
                    patient=self.patient,
                    source_system=SOURCE_SYSTEM,
                    fhir_id="observation-1",
                )

        other_patient = Patient.objects.create(
            source_system="https://synthetic.example/fhir",
            fhir_id="patient-1",
        )
        Observation.objects.create(
            patient=other_patient,
            source_system=other_patient.source_system,
            fhir_id="observation-1",
        )
        self.assertEqual(Observation.objects.count(), 2)

    def test_non_quantity_choice_and_repeating_fields_are_preserved(self):
        observation = Observation.objects.create(
            patient=self.patient,
            source_system=SOURCE_SYSTEM,
            fhir_id="observation-boolean",
            categories=[{"coding": [{"code": "survey"}]}],
            code_codings=[
                {
                    "system": "http://loinc.org",
                    "code": "synthetic-code",
                    "display": "Synthetic answer",
                }
            ],
            value_type="valueBoolean",
            value=False,
            components=[
                {
                    "code": {"text": "Synthetic component"},
                    "valueString": "example",
                }
            ],
            reference_ranges=[{"text": "Synthetic reference range"}],
            effective_type="effectivePeriod",
            effective={"start": "2024-01", "end": "2024-02"},
        )

        observation.refresh_from_db()

        self.assertIs(observation.value, False)
        self.assertEqual(observation.value_type, "valueBoolean")
        self.assertEqual(observation.code_codings[0]["system"], "http://loinc.org")
        self.assertEqual(observation.components[0]["valueString"], "example")
        self.assertEqual(
            observation.reference_ranges[0]["text"],
            "Synthetic reference range",
        )
        self.assertEqual(observation.effective["start"], "2024-01")

    def test_patient_reverse_relation_and_protection_are_enforced(self):
        observation = Observation.objects.create(
            patient=self.patient,
            source_system=SOURCE_SYSTEM,
            fhir_id="observation-1",
        )

        self.assertEqual(list(self.patient.observations.all()), [observation])

        with self.assertRaises(ProtectedError):
            self.patient.delete()

        self.assertTrue(Patient.objects.filter(pk=self.patient.pk).exists())
        self.assertTrue(Observation.objects.filter(pk=observation.pk).exists())

    def test_observation_and_patient_source_systems_must_match(self):
        observation = Observation(
            patient=self.patient,
            source_system="https://synthetic.example/fhir",
            fhir_id="observation-cross-source",
        )

        with self.assertRaisesMessage(
            ValidationError,
            "Observation and Patient must use the same source system.",
        ):
            observation.full_clean()


class MigrationRunModelTests(TestCase):
    def test_defaults_support_checkpointing_and_reconciliation(self):
        run = MigrationRun.objects.create(source_system=SOURCE_SYSTEM)

        self.assertIsInstance(run.id, uuid.UUID)
        self.assertEqual(run.extraction_mode, MigrationRun.ExtractionMode.SEARCH)
        self.assertEqual(run.status, MigrationRun.Status.PENDING)
        self.assertEqual(run.checkpoint, {})
        self.assertEqual(run.request_count, 0)
        self.assertEqual(run.retry_count, 0)
        self.assertEqual(run.discovered_count, 0)
        self.assertEqual(run.parsed_count, 0)
        self.assertEqual(run.accepted_count, 0)
        self.assertEqual(run.rejected_count, 0)
        self.assertEqual(run.inserted_count, 0)
        self.assertEqual(run.updated_count, 0)
        self.assertEqual(run.unchanged_count, 0)

    def test_checkpoint_defaults_are_not_shared_between_runs(self):
        first = MigrationRun(source_system=SOURCE_SYSTEM)
        second = MigrationRun(source_system=SOURCE_SYSTEM)

        first.checkpoint["next_url"] = "https://synthetic.example/next"

        self.assertEqual(second.checkpoint, {})

    def test_checkpoint_is_persisted_as_json(self):
        run = MigrationRun.objects.create(
            source_system=SOURCE_SYSTEM,
            checkpoint={"patient_page": 2, "completed_patient_ids": ["patient-1"]},
        )

        run.refresh_from_db()

        self.assertEqual(run.checkpoint["patient_page"], 2)
        self.assertEqual(run.checkpoint["completed_patient_ids"], ["patient-1"])

    def test_invalid_status_and_negative_counters_fail_model_validation(self):
        invalid_status = MigrationRun(
            source_system=SOURCE_SYSTEM,
            status="not-a-status",
        )
        negative_counter = MigrationRun(
            source_system=SOURCE_SYSTEM,
            request_count=-1,
        )

        with self.assertRaises(ValidationError):
            invalid_status.full_clean()
        with self.assertRaises(ValidationError):
            negative_counter.full_clean()
