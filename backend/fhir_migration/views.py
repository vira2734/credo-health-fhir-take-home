from django.db.models import Prefetch
from rest_framework import viewsets

from fhir_migration.models import Observation, Patient
from fhir_migration.serializers import (
    PatientDetailSerializer,
    PatientListSerializer,
)


class PatientViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Patient.objects.order_by("display_name", "fhir_id")

    def get_queryset(self):
        queryset = super().get_queryset()
        if self.action == "retrieve":
            queryset = queryset.prefetch_related(
                Prefetch(
                    "observations",
                    queryset=Observation.objects.order_by("-effective_at", "fhir_id"),
                )
            )
        return queryset

    def get_serializer_class(self):
        if self.action == "retrieve":
            return PatientDetailSerializer
        return PatientListSerializer
