import uuid

from django.core.exceptions import ValidationError
from django.db import models


class Patient(models.Model):
    """Simplified Patient record with lossless synthetic FHIR context."""

    source_system = models.CharField(max_length=255)
    fhir_id = models.CharField(max_length=255)
    source_version_id = models.CharField(max_length=255, blank=True)
    source_last_updated = models.DateTimeField(null=True, blank=True)

    names = models.JSONField(default=list, blank=True)
    display_name = models.CharField(max_length=255, blank=True)
    gender = models.CharField(max_length=32, blank=True)
    # FHIR dates can be year-only or year-month, so preserve the source precision.
    birth_date = models.CharField(max_length=10, null=True, blank=True)
    active = models.BooleanField(null=True, blank=True)

    identifiers = models.JSONField(default=list, blank=True)
    telecom = models.JSONField(default=list, blank=True)
    addresses = models.JSONField(default=list, blank=True)
    communications = models.JSONField(default=list, blank=True)
    raw_resource = models.JSONField(default=dict, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("source_system", "fhir_id"),
                name="uniq_patient_source_fhir_id",
            )
        ]

    def __str__(self) -> str:
        return f"{self.source_system}:{self.fhir_id}"


class Observation(models.Model):
    """Simplified Observation that preserves FHIR choice and repeating fields."""

    patient = models.ForeignKey(
        Patient,
        on_delete=models.PROTECT,
        related_name="observations",
    )
    source_system = models.CharField(max_length=255)
    fhir_id = models.CharField(max_length=255)
    source_version_id = models.CharField(max_length=255, blank=True)
    source_last_updated = models.DateTimeField(null=True, blank=True)

    status = models.CharField(max_length=32, blank=True)
    categories = models.JSONField(default=list, blank=True)
    code_codings = models.JSONField(default=list, blank=True)
    code_text = models.TextField(blank=True)
    display_label = models.CharField(max_length=255, blank=True)

    value_type = models.CharField(max_length=64, blank=True)
    value = models.JSONField(null=True, blank=True)
    value_numeric = models.DecimalField(
        max_digits=38,
        decimal_places=18,
        null=True,
        blank=True,
    )
    value_text = models.TextField(blank=True)
    value_unit = models.CharField(max_length=255, blank=True)
    data_absent_reason = models.JSONField(null=True, blank=True)
    components = models.JSONField(default=list, blank=True)
    reference_ranges = models.JSONField(default=list, blank=True)

    effective_type = models.CharField(max_length=64, blank=True)
    effective = models.JSONField(null=True, blank=True)
    effective_at = models.DateTimeField(null=True, blank=True)
    issued = models.DateTimeField(null=True, blank=True)
    raw_resource = models.JSONField(default=dict, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("source_system", "fhir_id"),
                name="uniq_observation_source_fhir_id",
            )
        ]
        indexes = [
            models.Index(
                fields=("patient", "effective_at"),
                name="obs_patient_effective_idx",
            )
        ]

    def __str__(self) -> str:
        return f"{self.source_system}:{self.fhir_id}"

    def clean(self) -> None:
        super().clean()
        if not self.patient_id or not self.source_system:
            return

        patient_source_system = (
            Patient.objects.filter(pk=self.patient_id)
            .values_list("source_system", flat=True)
            .first()
        )
        if (
            patient_source_system is not None
            and patient_source_system != self.source_system
        ):
            raise ValidationError(
                {
                    "source_system": (
                        "Observation and Patient must use the same source system."
                    )
                }
            )


class MigrationRun(models.Model):
    """Checkpoint and reconciliation counters for one extraction/load run."""

    class ExtractionMode(models.TextChoices):
        SEARCH = "search", "Paged search"
        BULK_EXPORT = "bulk_export", "Bulk export"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        RUNNING = "running", "Running"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    source_system = models.CharField(max_length=255)
    extraction_mode = models.CharField(
        max_length=32,
        choices=ExtractionMode.choices,
        default=ExtractionMode.SEARCH,
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
    )

    cutoff_at = models.DateTimeField(null=True, blank=True)
    source_transaction_time = models.DateTimeField(null=True, blank=True)
    checkpoint = models.JSONField(default=dict, blank=True)

    request_count = models.PositiveBigIntegerField(default=0)
    retry_count = models.PositiveBigIntegerField(default=0)
    discovered_count = models.PositiveBigIntegerField(default=0)
    parsed_count = models.PositiveBigIntegerField(default=0)
    accepted_count = models.PositiveBigIntegerField(default=0)
    rejected_count = models.PositiveBigIntegerField(default=0)
    inserted_count = models.PositiveBigIntegerField(default=0)
    updated_count = models.PositiveBigIntegerField(default=0)
    unchanged_count = models.PositiveBigIntegerField(default=0)

    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return f"{self.id}:{self.status}"
