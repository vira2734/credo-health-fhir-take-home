from rest_framework import serializers

from fhir_migration.models import Observation, Patient


class ObservationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Observation
        fields = (
            "id",
            "fhir_id",
            "status",
            "categories",
            "code_codings",
            "code_text",
            "display_label",
            "value_type",
            "value",
            "value_numeric",
            "value_text",
            "value_unit",
            "data_absent_reason",
            "components",
            "reference_ranges",
            "effective_type",
            "effective",
            "effective_at",
            "issued",
        )


class PatientListSerializer(serializers.ModelSerializer):
    class Meta:
        model = Patient
        fields = (
            "id",
            "fhir_id",
            "display_name",
            "gender",
            "birth_date",
            "active",
        )


class PatientDetailSerializer(PatientListSerializer):
    observations = ObservationSerializer(many=True, read_only=True)

    class Meta(PatientListSerializer.Meta):
        fields = (*PatientListSerializer.Meta.fields, "observations")
