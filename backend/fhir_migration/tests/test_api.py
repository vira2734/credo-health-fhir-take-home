from datetime import datetime, timezone
from decimal import Decimal

from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from fhir_migration.models import Observation, Patient


SOURCE_SYSTEM = "https://hapi.fhir.org/baseR4"


class PatientApiTests(APITestCase):
    def make_patient(self, fhir_id, display_name):
        return Patient.objects.create(
            source_system=SOURCE_SYSTEM,
            fhir_id=fhir_id,
            display_name=display_name,
            gender="unknown",
            birth_date="1980-04",
            active=True,
            raw_resource={"resourceType": "Patient", "id": fhir_id},
        )

    def test_list_returns_ordered_patient_summaries_without_raw_fhir(self):
        self.make_patient("patient-2", "Synthetic Zed")
        first = self.make_patient("patient-1", "Synthetic Alpha")
        Observation.objects.create(
            patient=first,
            source_system=SOURCE_SYSTEM,
            fhir_id="observation-1",
        )

        response = self.client.get(reverse("patient-list"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            [patient["fhir_id"] for patient in response.json()],
            ["patient-1", "patient-2"],
        )
        self.assertEqual(
            set(response.json()[0]),
            {"id", "fhir_id", "display_name", "gender", "birth_date", "active"},
        )
        self.assertNotIn("raw_resource", response.json()[0])
        self.assertNotIn("observations", response.json()[0])

    def test_detail_returns_nested_observations_without_raw_fhir(self):
        patient = self.make_patient("patient-1", "Synthetic Alpha")
        Observation.objects.create(
            patient=patient,
            source_system=SOURCE_SYSTEM,
            fhir_id="observation-1",
            status="final",
            code_text="Synthetic body temperature",
            display_label="Body temperature",
            value_type="valueQuantity",
            value={"value": 98.6, "unit": "degrees F"},
            value_numeric=Decimal("98.6"),
            value_unit="degrees F",
            effective_type="effectiveDateTime",
            effective="2024-03-04T05:00:00Z",
            effective_at=datetime(2024, 3, 4, 5, tzinfo=timezone.utc),
            raw_resource={"resourceType": "Observation", "id": "observation-1"},
        )
        Observation.objects.create(
            patient=patient,
            source_system=SOURCE_SYSTEM,
            fhir_id="observation-2",
            status="final",
            display_label="Synthetic boolean result",
            value_type="valueBoolean",
            value=False,
            effective_type="effectivePeriod",
            effective={"start": "2024-03"},
        )

        response = self.client.get(
            reverse("patient-detail", kwargs={"pk": patient.pk})
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        payload = response.json()
        self.assertEqual(payload["fhir_id"], "patient-1")
        self.assertEqual(
            set(payload),
            {
                "id",
                "fhir_id",
                "display_name",
                "gender",
                "birth_date",
                "active",
                "observations",
            },
        )
        self.assertEqual(len(payload["observations"]), 2)
        observation = payload["observations"][0]
        self.assertEqual(observation["fhir_id"], "observation-1")
        self.assertEqual(observation["status"], "final")
        self.assertEqual(observation["display_label"], "Body temperature")
        self.assertEqual(observation["value_type"], "valueQuantity")
        self.assertEqual(observation["value"], {"value": 98.6, "unit": "degrees F"})
        self.assertEqual(observation["value_numeric"], "98.600000000000000000")
        self.assertEqual(observation["value_unit"], "degrees F")
        self.assertEqual(observation["effective_type"], "effectiveDateTime")
        self.assertEqual(observation["effective"], "2024-03-04T05:00:00Z")
        self.assertNotIn("raw_resource", observation)
        boolean_observation = payload["observations"][1]
        self.assertEqual(boolean_observation["value_type"], "valueBoolean")
        self.assertIs(boolean_observation["value"], False)

    def test_detail_returns_404_for_unknown_patient(self):
        response = self.client.get(reverse("patient-detail", kwargs={"pk": 999}))

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
