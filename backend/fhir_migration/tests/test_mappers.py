from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import math

from django.test import SimpleTestCase

from fhir_migration.services.mappers import (
    FhirMappingError,
    MappedObservation,
    map_observation,
    map_patient,
)

SOURCE_SYSTEM = "https://hapi.fhir.org/baseR4"


class PatientMapperTests(SimpleTestCase):
    def test_patient_mapping_preserves_fhir_fields_and_false_values(self):
        resource = {
            "resourceType": "Patient",
            "id": "patient-1",
            "meta": {
                "versionId": "3",
                "lastUpdated": "2024-03-04T05:06:07Z",
            },
            "name": [
                {
                    "use": "official",
                    "text": "Synthetic Patient",
                    "family": "Patient",
                    "given": ["Synthetic"],
                }
            ],
            "gender": "unknown",
            "birthDate": "1980-04",
            "active": False,
            "identifier": [{"system": "urn:synthetic", "value": "patient-1"}],
            "telecom": [{"system": "email", "value": "example@example.invalid"}],
            "address": [{"city": "Exampleville"}],
            "communication": [{"language": {"text": "English"}}],
        }
        original = deepcopy(resource)

        mapped = map_patient(resource, SOURCE_SYSTEM)

        self.assertEqual(resource, original)
        self.assertEqual(mapped["source_system"], SOURCE_SYSTEM)
        self.assertEqual(mapped["fhir_id"], "patient-1")
        self.assertEqual(mapped["source_version_id"], "3")
        self.assertEqual(
            mapped["source_last_updated"],
            datetime(2024, 3, 4, 5, 6, 7, tzinfo=timezone.utc),
        )
        self.assertEqual(mapped["display_name"], "Synthetic Patient")
        self.assertEqual(mapped["gender"], "unknown")
        self.assertIs(mapped["active"], False)
        self.assertEqual(mapped["birth_date"], "1980-04")
        self.assertEqual(mapped["names"], resource["name"])
        self.assertEqual(mapped["identifiers"], resource["identifier"])
        self.assertEqual(mapped["telecom"], resource["telecom"])
        self.assertEqual(mapped["addresses"], resource["address"])
        self.assertEqual(mapped["communications"], resource["communication"])
        self.assertEqual(mapped["raw_resource"], original)

    def test_patient_mapping_is_null_safe_and_preserves_partial_dates(self):
        for birth_date in (None, "1980", "1980-04", "1980-04-03"):
            with self.subTest(birth_date=birth_date):
                resource = {"resourceType": "Patient", "id": "patient-1"}
                if birth_date is not None:
                    resource["birthDate"] = birth_date

                mapped = map_patient(resource, SOURCE_SYSTEM)

                self.assertEqual(mapped["birth_date"], birth_date)
                self.assertEqual(mapped["names"], [])
                self.assertEqual(mapped["display_name"], "")
                self.assertIsNone(mapped["active"])
                self.assertEqual(mapped["identifiers"], [])
                self.assertEqual(mapped["telecom"], [])
                self.assertEqual(mapped["addresses"], [])
                self.assertEqual(mapped["communications"], [])

    def test_patient_display_name_falls_back_to_given_and_family(self):
        resource = {
            "resourceType": "Patient",
            "id": "patient-1",
            "name": [{"given": ["Synthetic", "Example"], "family": "Person"}],
        }

        mapped = map_patient(resource, SOURCE_SYSTEM)

        self.assertEqual(mapped["display_name"], "Synthetic Example Person")

    def test_patient_rejects_invalid_identity_and_birth_date(self):
        invalid_resources = (
            {"resourceType": "Observation", "id": "patient-1"},
            {"resourceType": "Patient"},
            {"resourceType": "Patient", "id": ""},
            {"resourceType": "Patient", "id": "patient-1", "birthDate": "1980-13"},
            {
                "resourceType": "Patient",
                "id": "patient-1",
                "birthDate": "1980-02-30",
            },
            {"resourceType": "Patient", "id": "patient-1", "birthDate": 1980},
        )

        for resource in invalid_resources:
            with self.subTest(resource=resource):
                with self.assertRaises(FhirMappingError) as raised:
                    map_patient(resource, SOURCE_SYSTEM)
                self.assertNotIn("1980-02-30", str(raised.exception))


class ObservationMapperTests(SimpleTestCase):
    def observation(self, **overrides):
        resource = {
            "resourceType": "Observation",
            "id": "observation-1",
            "subject": {"reference": "Patient/patient-1"},
            "status": "final",
            "code": {
                "coding": [
                    {
                        "system": "http://loinc.org",
                        "code": "synthetic-code",
                        "display": "Synthetic measurement",
                    }
                ]
            },
        }
        resource.update(overrides)
        return resource

    def test_quantity_mapping_preserves_full_value_and_query_projections(self):
        resource = self.observation(
            meta={"versionId": "2", "lastUpdated": "2024-03-04T05:06:07Z"},
            category=[{"coding": [{"code": "vital-signs"}]}],
            code={
                "coding": [
                    {
                        "system": "http://loinc.org",
                        "code": "8310-5",
                        "display": "Body temperature",
                    }
                ],
                "text": "Synthetic body temperature",
            },
            valueQuantity={
                "value": 98.6,
                "comparator": "<",
                "unit": "degrees F",
                "system": "http://unitsofmeasure.org",
                "code": "[degF]",
            },
            component=[{"code": {"text": "Synthetic component"}, "valueInteger": 0}],
            referenceRange=[{"text": "Synthetic range"}],
            effectiveDateTime="2024-03-04T05:00:00Z",
            issued="2024-03-04T05:06:07-07:00",
        )
        original = deepcopy(resource)

        mapped = map_observation(resource, SOURCE_SYSTEM)

        self.assertIsInstance(mapped, MappedObservation)
        self.assertEqual(mapped.patient_fhir_id, "patient-1")
        attrs = mapped.attributes
        self.assertEqual(resource, original)
        self.assertEqual(attrs["source_system"], SOURCE_SYSTEM)
        self.assertEqual(attrs["fhir_id"], "observation-1")
        self.assertEqual(attrs["source_version_id"], "2")
        self.assertEqual(
            attrs["source_last_updated"],
            datetime(2024, 3, 4, 5, 6, 7, tzinfo=timezone.utc),
        )
        self.assertEqual(attrs["status"], "final")
        self.assertEqual(attrs["categories"], resource["category"])
        self.assertEqual(attrs["code_codings"], resource["code"]["coding"])
        self.assertEqual(attrs["code_text"], "Synthetic body temperature")
        self.assertEqual(attrs["display_label"], "Synthetic body temperature")
        self.assertEqual(attrs["value_type"], "valueQuantity")
        self.assertEqual(attrs["value"], resource["valueQuantity"])
        self.assertEqual(attrs["value_numeric"], Decimal("98.6"))
        self.assertEqual(attrs["value_unit"], "degrees F")
        self.assertEqual(attrs["components"], resource["component"])
        self.assertEqual(attrs["reference_ranges"], resource["referenceRange"])
        self.assertEqual(attrs["effective_type"], "effectiveDateTime")
        self.assertEqual(attrs["effective"], "2024-03-04T05:00:00Z")
        self.assertEqual(
            attrs["effective_at"],
            datetime(2024, 3, 4, 5, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(
            attrs["issued"],
            datetime(
                2024,
                3,
                4,
                5,
                6,
                7,
                tzinfo=timezone(timedelta(hours=-7)),
            ),
        )
        self.assertEqual(attrs["raw_resource"], original)

    def test_quantity_projection_rejects_non_fhir_or_non_finite_numbers(self):
        invalid_values = ("98.6", True, math.nan, math.inf, -math.inf)

        for value in invalid_values:
            with self.subTest(value=value):
                with self.assertRaises(FhirMappingError):
                    map_observation(
                        self.observation(valueQuantity={"value": value}),
                        SOURCE_SYSTEM,
                    )

    def test_all_r4_value_choices_are_preserved_by_key_presence(self):
        choices = {
            "valueQuantity": {"value": 0, "unit": "synthetic"},
            "valueCodeableConcept": {"text": "Synthetic concept"},
            "valueString": "Synthetic text",
            "valueBoolean": False,
            "valueInteger": 0,
            "valueRange": {"low": {"value": 0}, "high": {"value": 1}},
            "valueRatio": {
                "numerator": {"value": 1},
                "denominator": {"value": 2},
            },
            "valueSampledData": {"origin": {"value": 0}, "data": "0 1"},
            "valueTime": "12:34:56",
            "valueDateTime": "2024-03",
            "valuePeriod": {"start": "2024-01", "end": "2024-02"},
        }

        for field, value in choices.items():
            with self.subTest(field=field):
                mapped = map_observation(
                    self.observation(**{field: value}),
                    SOURCE_SYSTEM,
                )
                self.assertEqual(mapped.attributes["value_type"], field)
                self.assertEqual(mapped.attributes["value"], value)
                if field == "valueString":
                    self.assertEqual(mapped.attributes["value_text"], value)

    def test_missing_value_is_allowed_with_data_absent_reason_and_components(self):
        resource = self.observation(
            dataAbsentReason={"coding": [{"code": "unknown"}]},
            component=[{"code": {"text": "Synthetic component"}, "valueBoolean": False}],
        )

        mapped = map_observation(resource, SOURCE_SYSTEM)

        self.assertEqual(mapped.attributes["value_type"], "")
        self.assertIsNone(mapped.attributes["value"])
        self.assertEqual(
            mapped.attributes["data_absent_reason"],
            resource["dataAbsentReason"],
        )
        self.assertIs(mapped.attributes["components"][0]["valueBoolean"], False)

    def test_choice_invariants_reject_ambiguous_or_invalid_results(self):
        invalid_resources = (
            self.observation(valueString="one", valueBoolean=False),
            self.observation(
                effectiveDateTime="2024-01",
                effectivePeriod={"start": "2024-01"},
            ),
            self.observation(
                valueString="synthetic",
                dataAbsentReason={"text": "should not coexist"},
            ),
        )

        for resource in invalid_resources:
            with self.subTest(resource=resource):
                with self.assertRaises(FhirMappingError):
                    map_observation(resource, SOURCE_SYSTEM)

    def test_subject_must_be_an_exact_relative_patient_reference(self):
        invalid_subjects = (
            None,
            {},
            {"reference": "Group/group-1"},
            {"reference": "https://example.invalid/Patient/patient-1"},
            {"reference": "Patient/patient-1/_history/2"},
            {"reference": "Patient/"},
        )

        for subject in invalid_subjects:
            with self.subTest(subject=subject):
                with self.assertRaises(FhirMappingError):
                    map_observation(
                        self.observation(subject=subject),
                        SOURCE_SYSTEM,
                    )

    def test_effective_choices_are_preserved_and_only_full_times_are_projected(self):
        cases = (
            ("effectiveDateTime", "2024-03", None),
            (
                "effectiveDateTime",
                "2024-03-04T05:06:07Z",
                datetime(2024, 3, 4, 5, 6, 7, tzinfo=timezone.utc),
            ),
            ("effectivePeriod", {"start": "2024-03"}, None),
            ("effectiveTiming", {"repeat": {"count": 1}}, None),
            (
                "effectiveInstant",
                "2024-03-04T05:06:07Z",
                datetime(2024, 3, 4, 5, 6, 7, tzinfo=timezone.utc),
            ),
        )

        for field, value, expected_timestamp in cases:
            with self.subTest(field=field, value=value):
                mapped = map_observation(
                    self.observation(**{field: value}),
                    SOURCE_SYSTEM,
                )
                self.assertEqual(mapped.attributes["effective_type"], field)
                self.assertEqual(mapped.attributes["effective"], value)
                self.assertEqual(mapped.attributes["effective_at"], expected_timestamp)

    def test_display_label_fallback_is_deterministic(self):
        cases = (
            ({"text": "Code text", "coding": [{"display": "Display"}]}, "Code text"),
            ({"coding": [{"display": "Display", "code": "code"}]}, "Display"),
            ({"coding": [{"code": "code"}]}, "code"),
            ({}, ""),
        )

        for code, expected in cases:
            with self.subTest(code=code):
                mapped = map_observation(
                    self.observation(code=code),
                    SOURCE_SYSTEM,
                )
                self.assertEqual(mapped.attributes["display_label"], expected)

    def test_observation_rejects_invalid_identity_and_timestamps(self):
        invalid_resources = (
            self.observation(resourceType="Patient"),
            {"resourceType": "Observation"},
            self.observation(id=""),
            self.observation(meta={"lastUpdated": "not-a-timestamp"}),
            self.observation(issued="2024-03-04T05:06:07"),
            self.observation(effectiveInstant="2024-03-04T05:06:07"),
            self.observation(effectiveDateTime="2024-13"),
        )

        for resource in invalid_resources:
            with self.subTest(resource=resource):
                with self.assertRaises(FhirMappingError) as raised:
                    map_observation(resource, SOURCE_SYSTEM)
                self.assertNotIn("synthetic-sensitive-clinical-marker", str(raised.exception))

        sensitive_resource = self.observation(
            resourceType="Patient",
            code={"text": "synthetic-sensitive-clinical-marker"},
        )
        with self.assertRaises(FhirMappingError) as raised:
            map_observation(sensitive_resource, SOURCE_SYSTEM)
        self.assertNotIn("synthetic-sensitive-clinical-marker", str(raised.exception))

    def test_timestamp_projections_reject_invalid_or_unrepresentable_values(self):
        invalid_resources = (
            self.observation(issued="2024-03-04T05:06Z"),
            self.observation(issued="2024-03-04 05:06:07Z"),
            self.observation(issued="2024-02-30T05:06:07Z"),
            self.observation(effectiveInstant="2024-03-04T05:06:07+0000"),
            self.observation(effectiveInstant="0000-03-04T05:06:07Z"),
            self.observation(effectiveInstant="2024-03-04T05:06:60Z"),
            self.observation(effectiveDateTime="2024-03-04T05:06Z"),
            self.observation(effectiveDateTime="2024-03-04 05:06:07Z"),
            self.observation(effectiveDateTime="2024-02-30T05:06:07Z"),
            self.observation(valueDateTime="2024-02-30T05:06:07Z"),
        )

        for resource in invalid_resources:
            with self.subTest(resource=resource):
                with self.assertRaises(FhirMappingError):
                    map_observation(resource, SOURCE_SYSTEM)
