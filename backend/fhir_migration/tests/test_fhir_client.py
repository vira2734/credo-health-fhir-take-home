from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from unittest.mock import Mock

import requests
from django.test import SimpleTestCase

from fhir_migration.services.fhir_client import (
    FhirClient,
    FhirClientError,
    FhirProtocolError,
)

BASE_URL = "https://hapi.fhir.org/baseR4"


class FakeResponse:
    def __init__(self, payload=None, *, status_code=200, headers=None, json_error=None):
        self.payload = payload
        self.status_code = status_code
        self.headers = headers or {}
        self.json_error = json_error
        self.text = "synthetic-sensitive-body-marker"

    def json(self):
        if self.json_error is not None:
            raise self.json_error
        return self.payload


class FakeSession:
    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if not self.outcomes:
            raise AssertionError("Unexpected GET request")
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def entry(resource_type, fhir_id, *, search_mode="match"):
    return {
        "resource": {"resourceType": resource_type, "id": fhir_id},
        "search": {"mode": search_mode},
    }


def search_bundle(*entries, links=None):
    bundle = {
        "resourceType": "Bundle",
        "type": "searchset",
        "entry": list(entries),
    }
    if links is not None:
        bundle["link"] = links
    return bundle


class FhirClientTests(SimpleTestCase):
    def make_client(self, session, **overrides):
        options = {
            "session": session,
            "patient_page_size": 100,
            "observation_page_size": 2,
            "connect_timeout": 1.5,
            "read_timeout": 4.5,
            "max_retries": 2,
            "backoff_factor": 0.5,
            "max_backoff": 8.0,
            "sleeper": Mock(),
            "random_fn": lambda: 0.0,
        }
        options.update(overrides)
        return FhirClient(BASE_URL + "/", **options)

    def test_patient_limit_bounds_count_and_avoids_unneeded_next_request(self):
        next_url = f"{BASE_URL}?_getpages=opaque"
        session = FakeSession(
            FakeResponse(
                search_bundle(
                    entry("Patient", "patient-1"),
                    entry("Patient", "patient-2"),
                    links=[{"relation": "next", "url": next_url}],
                )
            )
        )
        client = self.make_client(session)

        patients = list(client.iter_patients(limit=1))

        self.assertEqual([patient["id"] for patient in patients], ["patient-1"])
        self.assertEqual(len(session.calls), 1)
        url, kwargs = session.calls[0]
        self.assertEqual(url, f"{BASE_URL}/Patient")
        self.assertEqual(kwargs["params"], {"_count": 1})
        self.assertEqual(kwargs["headers"], {"Accept": "application/fhir+json"})
        self.assertEqual(kwargs["timeout"], (1.5, 4.5))

    def test_patient_pagination_follows_opaque_next_link_exactly(self):
        next_url = f"{BASE_URL}?_getpages=opaque-state&_getpagesoffset=1"
        session = FakeSession(
            FakeResponse(
                search_bundle(
                    entry("Patient", "patient-1"),
                    links=[
                        {"relation": "self", "url": f"{BASE_URL}/Patient"},
                        {"relation": "next", "url": next_url},
                    ],
                )
            ),
            FakeResponse(search_bundle(entry("Patient", "patient-2"))),
        )
        client = self.make_client(session)

        patients = list(client.iter_patients(limit=2))

        self.assertEqual(
            [patient["id"] for patient in patients],
            ["patient-1", "patient-2"],
        )
        self.assertEqual(session.calls[1][0], next_url)
        self.assertIsNone(session.calls[1][1].get("params"))
        self.assertEqual(
            session.calls[1][1]["headers"],
            {"Accept": "application/fhir+json"},
        )
        self.assertEqual(session.calls[1][1]["timeout"], (1.5, 4.5))

    def test_observation_search_uses_subject_and_shared_pagination(self):
        next_url = f"{BASE_URL}?_getpages=observation-state"
        session = FakeSession(
            FakeResponse(
                search_bundle(
                    entry("Observation", "observation-1"),
                    links=[{"relation": "next", "url": next_url}],
                )
            ),
            FakeResponse(search_bundle(entry("Observation", "observation-2"))),
        )
        client = self.make_client(session)

        observations = list(client.iter_observations("patient-1"))

        self.assertEqual(
            [observation["id"] for observation in observations],
            ["observation-1", "observation-2"],
        )
        self.assertEqual(session.calls[0][0], f"{BASE_URL}/Observation")
        self.assertEqual(
            session.calls[0][1]["params"],
            {"subject": "Patient/patient-1", "_count": 2},
        )
        self.assertEqual(session.calls[1][0], next_url)

    def test_search_skips_warning_operation_outcome_entries(self):
        warning = {
            "resource": {
                "resourceType": "OperationOutcome",
                "issue": [{"diagnostics": "synthetic warning"}],
            },
            "search": {"mode": "outcome"},
        }
        session = FakeSession(
            FakeResponse(
                search_bundle(
                    warning,
                    entry("Patient", "patient-1"),
                )
            )
        )

        patients = list(self.make_client(session).iter_patients(limit=2))

        self.assertEqual([patient["id"] for patient in patients], ["patient-1"])

    def test_empty_search_bundles_are_valid(self):
        for payload in (
            {"resourceType": "Bundle", "type": "searchset"},
            search_bundle(),
        ):
            with self.subTest(payload=payload):
                session = FakeSession(FakeResponse(payload))
                self.assertEqual(
                    list(self.make_client(session).iter_observations("patient-1")),
                    [],
                )

    def test_all_configured_transient_failures_retry_then_succeed(self):
        transient_failures = (
            requests.ConnectionError("synthetic connection failure"),
            requests.Timeout("synthetic timeout"),
            FakeResponse(status_code=429),
            FakeResponse(status_code=502),
            FakeResponse(status_code=503),
            FakeResponse(status_code=504),
        )

        for failure in transient_failures:
            with self.subTest(failure=failure):
                sleeper = Mock()
                session = FakeSession(
                    failure,
                    FakeResponse(search_bundle(entry("Patient", "patient-1"))),
                )
                client = self.make_client(session, sleeper=sleeper)

                self.assertEqual(len(list(client.iter_patients(limit=1))), 1)
                sleeper.assert_called_once_with(0.5)

    def test_retry_after_seconds_and_http_date_are_honored(self):
        for status_code in (429, 503):
            with self.subTest(status_code=status_code, header="seconds"):
                sleeper = Mock()
                session = FakeSession(
                    FakeResponse(
                        status_code=status_code,
                        headers={"Retry-After": "7"},
                    ),
                    FakeResponse(search_bundle(entry("Patient", "patient-1"))),
                )
                client = self.make_client(session, sleeper=sleeper)

                self.assertEqual(len(list(client.iter_patients(limit=1))), 1)
                sleeper.assert_called_once_with(7.0)

        with self.subTest(header="HTTP date"):
            now = datetime(2024, 3, 4, 5, 6, 7, tzinfo=timezone.utc)
            retry_at = format_datetime(now + timedelta(seconds=6), usegmt=True)
            sleeper = Mock()
            session = FakeSession(
                FakeResponse(status_code=503, headers={"Retry-After": retry_at}),
                FakeResponse(search_bundle(entry("Patient", "patient-1"))),
            )
            client = self.make_client(
                session,
                sleeper=sleeper,
                now_fn=lambda: now,
            )

            self.assertEqual(len(list(client.iter_patients(limit=1))), 1)
            sleeper.assert_called_once_with(6.0)

    def test_retry_after_cannot_bypass_the_backoff_bound(self):
        now = datetime(2024, 3, 4, 5, 6, 7, tzinfo=timezone.utc)
        far_future = format_datetime(now + timedelta(days=365), usegmt=True)
        cases = (
            ("999999", 8.0),
            (far_future, 8.0),
            ("Infinity", 0.5),
        )

        for retry_after, expected_delay in cases:
            with self.subTest(retry_after=retry_after):
                sleeper = Mock()
                session = FakeSession(
                    FakeResponse(
                        status_code=503,
                        headers={"Retry-After": retry_after},
                    ),
                    FakeResponse(search_bundle(entry("Patient", "patient-1"))),
                )
                client = self.make_client(
                    session,
                    sleeper=sleeper,
                    now_fn=lambda: now,
                )

                self.assertEqual(len(list(client.iter_patients(limit=1))), 1)
                sleeper.assert_called_once_with(expected_delay)

    def test_permanent_failure_is_immediate_and_sanitized(self):
        session = FakeSession(
            FakeResponse(
                {
                    "resourceType": "OperationOutcome",
                    "issue": [{"diagnostics": "synthetic-sensitive-body-marker"}],
                },
                status_code=400,
            )
        )
        client = self.make_client(session)

        with self.assertRaises(FhirClientError) as raised:
            list(client.iter_patients(limit=1))

        self.assertEqual(len(session.calls), 1)
        self.assertIn("400", str(raised.exception))
        self.assertIn("Patient", str(raised.exception))
        self.assertNotIn("synthetic-sensitive-body-marker", str(raised.exception))

    def test_transient_failure_exhaustion_is_bounded(self):
        session = FakeSession(
            FakeResponse(status_code=503),
            FakeResponse(status_code=503),
            FakeResponse(status_code=503),
        )
        sleeper = Mock()
        client = self.make_client(session, sleeper=sleeper)

        with self.assertRaises(FhirClientError):
            list(client.iter_patients(limit=1))

        self.assertEqual(len(session.calls), 3)
        self.assertEqual(sleeper.call_args_list[0].args, (0.5,))
        self.assertEqual(sleeper.call_args_list[1].args, (1.0,))

    def test_unlisted_500_failure_is_not_retried(self):
        session = FakeSession(FakeResponse(status_code=500))

        with self.assertRaises(FhirClientError):
            list(self.make_client(session).iter_patients(limit=1))

        self.assertEqual(len(session.calls), 1)

    def test_malformed_search_responses_raise_protocol_errors(self):
        cases = {
            "invalid JSON": FakeResponse(json_error=ValueError("invalid JSON")),
            "not a Bundle": FakeResponse({"resourceType": "Patient"}),
            "not a searchset": FakeResponse(
                {"resourceType": "Bundle", "type": "collection"}
            ),
            "malformed entry": FakeResponse(
                search_bundle({"search": {"mode": "match"}})
            ),
            "wrong resource type": FakeResponse(
                search_bundle(entry("Observation", "observation-1"))
            ),
        }

        for label, response in cases.items():
            with self.subTest(label):
                client = self.make_client(FakeSession(response))
                with self.assertRaises(FhirProtocolError):
                    list(client.iter_patients(limit=1))

    def test_cross_origin_and_repeated_next_links_are_rejected(self):
        with self.subTest("cross-origin"):
            session = FakeSession(
                FakeResponse(
                    search_bundle(
                        entry("Patient", "patient-1"),
                        links=[
                            {
                                "relation": "next",
                                "url": "https://unexpected.example/next",
                            }
                        ],
                    )
                )
            )
            client = self.make_client(session)

            with self.assertRaises(FhirProtocolError):
                list(client.iter_patients(limit=2))
            self.assertEqual(len(session.calls), 1)

        with self.subTest("repeated link"):
            repeated_url = f"{BASE_URL}?_getpages=repeated"
            repeated_bundle = search_bundle(
                entry("Patient", "patient-1"),
                links=[{"relation": "next", "url": repeated_url}],
            )
            session = FakeSession(
                FakeResponse(repeated_bundle),
                FakeResponse(repeated_bundle),
            )
            client = self.make_client(session)

            with self.assertRaises(FhirProtocolError):
                list(client.iter_patients(limit=3))
            self.assertEqual(len(session.calls), 2)

    def test_stats_count_initial_request_retry_and_next_page_request(self):
        next_url = f"{BASE_URL}?_getpages=stats"
        session = FakeSession(
            FakeResponse(status_code=503),
            FakeResponse(
                search_bundle(
                    entry("Patient", "patient-1"),
                    links=[{"relation": "next", "url": next_url}],
                )
            ),
            FakeResponse(search_bundle(entry("Patient", "patient-2"))),
        )
        client = self.make_client(session)

        self.assertEqual(client.stats.request_count, 0)
        self.assertEqual(client.stats.retry_count, 0)
        self.assertEqual(len(list(client.iter_patients(limit=2))), 2)
        self.assertEqual(client.stats.request_count, 3)
        self.assertEqual(client.stats.retry_count, 1)

    def test_stats_count_exhausted_transport_attempts(self):
        session = FakeSession(
            requests.ConnectionError("synthetic failure"),
            requests.ConnectionError("synthetic failure"),
            requests.ConnectionError("synthetic failure"),
        )
        client = self.make_client(session)

        with self.assertRaises(FhirClientError):
            list(client.iter_patients(limit=1))

        self.assertEqual(client.stats.request_count, 3)
        self.assertEqual(client.stats.retry_count, 2)

    def test_stats_count_permanent_failure_without_retry(self):
        client = self.make_client(FakeSession(FakeResponse(status_code=400)))

        with self.assertRaises(FhirClientError):
            list(client.iter_patients(limit=1))

        self.assertEqual(client.stats.request_count, 1)
        self.assertEqual(client.stats.retry_count, 0)

    def test_stats_are_immutable_cumulative_snapshots(self):
        session = FakeSession(
            FakeResponse(search_bundle(entry("Patient", "patient-1"))),
            FakeResponse(search_bundle(entry("Observation", "observation-1"))),
        )
        client = self.make_client(session)

        before = client.stats
        list(client.iter_patients(limit=1))
        middle = client.stats
        list(client.iter_observations("patient-1"))
        after = client.stats

        self.assertEqual((before.request_count, before.retry_count), (0, 0))
        self.assertEqual((middle.request_count, middle.retry_count), (1, 0))
        self.assertEqual((after.request_count, after.retry_count), (2, 0))
        with self.assertRaises(AttributeError):
            after.request_count = 99

    def test_stats_do_not_count_a_retry_that_never_begins(self):
        def cancel_before_retry(_delay):
            raise RuntimeError("synthetic cancellation")

        client = self.make_client(
            FakeSession(FakeResponse(status_code=503)),
            sleeper=cancel_before_retry,
        )

        with self.assertRaises(RuntimeError):
            list(client.iter_patients(limit=1))

        self.assertEqual(client.stats.request_count, 1)
        self.assertEqual(client.stats.retry_count, 0)
