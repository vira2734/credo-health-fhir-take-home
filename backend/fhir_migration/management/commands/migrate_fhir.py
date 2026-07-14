"""Run a bounded synthetic FHIR Patient/Observation migration."""

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from fhir_migration.services.fhir_client import FhirClient
from fhir_migration.services.ingestion import MigrationFailed, run_fhir_migration


class Command(BaseCommand):
    help = "Migrate a bounded Patient sample and associated Observations"

    def add_arguments(self, parser):
        parser.add_argument("--patient-limit", type=int)

    def handle(self, *args, **options):
        patient_limit = options["patient_limit"]
        if patient_limit is None:
            patient_limit = settings.FHIR_DEFAULT_PATIENT_LIMIT
        if patient_limit < 1:
            raise CommandError("Patient limit must be positive")

        client = FhirClient(
            settings.FHIR_BASE_URL,
            patient_page_size=settings.FHIR_PATIENT_PAGE_SIZE,
            observation_page_size=settings.FHIR_OBSERVATION_PAGE_SIZE,
            connect_timeout=settings.FHIR_CONNECT_TIMEOUT,
            read_timeout=settings.FHIR_READ_TIMEOUT,
            max_retries=settings.FHIR_MAX_RETRIES,
            backoff_factor=settings.FHIR_BACKOFF_FACTOR,
            max_backoff=settings.FHIR_MAX_BACKOFF,
        )
        try:
            run = run_fhir_migration(client=client, patient_limit=patient_limit)
        except MigrationFailed as error:
            raise CommandError(str(error)) from None

        self.stdout.write(
            self.style.SUCCESS(
                f"run={run.id} status={run.status} "
                f"inserted={run.inserted_count} updated={run.updated_count} "
                f"unchanged={run.unchanged_count} rejected={run.rejected_count}"
            )
        )
