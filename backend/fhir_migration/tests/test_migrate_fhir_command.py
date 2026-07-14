from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from django.core.management import CommandError, call_command
from django.test import SimpleTestCase, override_settings

from fhir_migration.services.ingestion import MigrationFailed


@override_settings(
    FHIR_BASE_URL="https://example.test/fhir",
    FHIR_DEFAULT_PATIENT_LIMIT=10,
    FHIR_PATIENT_PAGE_SIZE=25,
    FHIR_OBSERVATION_PAGE_SIZE=50,
    FHIR_CONNECT_TIMEOUT=1.5,
    FHIR_READ_TIMEOUT=4.5,
    FHIR_MAX_RETRIES=3,
    FHIR_BACKOFF_FACTOR=0.25,
    FHIR_MAX_BACKOFF=8.0,
)
class MigrateFhirCommandTests(SimpleTestCase):
    def successful_run(self):
        return SimpleNamespace(
            id=uuid4(),
            status="succeeded",
            inserted_count=3,
            updated_count=1,
            unchanged_count=2,
            rejected_count=0,
        )

    @patch("fhir_migration.management.commands.migrate_fhir.run_fhir_migration")
    @patch("fhir_migration.management.commands.migrate_fhir.FhirClient")
    def test_command_builds_configured_client_and_forwards_limit(
        self,
        client_class,
        run_migration,
    ):
        client = client_class.return_value
        run_migration.return_value = self.successful_run()

        call_command("migrate_fhir", "--patient-limit", "3", stdout=StringIO())

        client_class.assert_called_once_with(
            "https://example.test/fhir",
            patient_page_size=25,
            observation_page_size=50,
            connect_timeout=1.5,
            read_timeout=4.5,
            max_retries=3,
            backoff_factor=0.25,
            max_backoff=8.0,
        )
        run_migration.assert_called_once_with(client=client, patient_limit=3)

    @patch("fhir_migration.management.commands.migrate_fhir.run_fhir_migration")
    @patch("fhir_migration.management.commands.migrate_fhir.FhirClient")
    def test_command_uses_default_and_prints_count_only_summary(
        self,
        client_class,
        run_migration,
    ):
        run = self.successful_run()
        run_migration.return_value = run
        stdout = StringIO()

        call_command("migrate_fhir", stdout=stdout)

        run_migration.assert_called_once_with(
            client=client_class.return_value,
            patient_limit=10,
        )
        output = stdout.getvalue()
        self.assertIn(str(run.id), output)
        self.assertIn("inserted=3", output)
        self.assertIn("updated=1", output)
        self.assertIn("unchanged=2", output)

    @patch("fhir_migration.management.commands.migrate_fhir.run_fhir_migration")
    def test_command_rejects_non_positive_limit(self, run_migration):
        with self.assertRaises(CommandError):
            call_command("migrate_fhir", "--patient-limit", "0")
        run_migration.assert_not_called()

    @patch("fhir_migration.management.commands.migrate_fhir.run_fhir_migration")
    @patch("fhir_migration.management.commands.migrate_fhir.FhirClient")
    def test_command_reports_sanitized_migration_failure(
        self,
        client_class,
        run_migration,
    ):
        failure = MigrationFailed(uuid4(), "extraction")
        run_migration.side_effect = failure

        with self.assertRaises(CommandError) as raised:
            call_command("migrate_fhir")

        self.assertEqual(str(raised.exception), str(failure))
        self.assertNotIn("synthetic-sensitive-clinical-marker", str(raised.exception))
