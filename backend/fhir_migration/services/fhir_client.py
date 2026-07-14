"""Small, bounded FHIR R4 search client for the take-home ingestion path."""

from collections.abc import Callable, Iterator, Mapping
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import math
import random
import re
import time
from typing import Any
from urllib.parse import urlsplit

import requests

FHIR_JSON = "application/fhir+json"
FHIR_ID_PATTERN = re.compile(r"^[A-Za-z0-9\-.]{1,64}$")
TRANSIENT_STATUS_CODES = frozenset({429, 502, 503, 504})


class FhirClientError(RuntimeError):
    """A sanitized transport or HTTP failure from the FHIR source."""


class FhirProtocolError(FhirClientError):
    """A successful response that does not match the expected FHIR shape."""


class FhirClient:
    def __init__(
        self,
        base_url: str,
        *,
        session: requests.Session | None = None,
        patient_page_size: int = 100,
        observation_page_size: int = 100,
        connect_timeout: float = 3.05,
        read_timeout: float = 20.0,
        max_retries: int = 2,
        backoff_factor: float = 0.5,
        max_backoff: float = 30.0,
        sleeper: Callable[[float], None] = time.sleep,
        random_fn: Callable[[], float] = random.random,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        canonical_base_url = base_url.rstrip("/")
        parsed_base_url = urlsplit(canonical_base_url)
        if parsed_base_url.scheme not in {"http", "https"} or not parsed_base_url.netloc:
            raise ValueError("FHIR base URL must be an absolute HTTP(S) URL")
        if patient_page_size < 1 or observation_page_size < 1:
            raise ValueError("FHIR page sizes must be positive")
        if (
            not math.isfinite(connect_timeout)
            or not math.isfinite(read_timeout)
            or connect_timeout <= 0
            or read_timeout <= 0
        ):
            raise ValueError("FHIR timeouts must be positive")
        if (
            max_retries < 0
            or not math.isfinite(backoff_factor)
            or not math.isfinite(max_backoff)
            or backoff_factor < 0
            or max_backoff < 0
        ):
            raise ValueError("FHIR retry settings cannot be negative")

        self.base_url = canonical_base_url
        self._origin = (
            parsed_base_url.scheme.lower(),
            parsed_base_url.netloc.lower(),
        )
        self._session = session or requests.Session()
        self._patient_page_size = patient_page_size
        self._observation_page_size = observation_page_size
        self._timeout = (connect_timeout, read_timeout)
        self._max_retries = max_retries
        self._backoff_factor = backoff_factor
        self._max_backoff = max_backoff
        self._sleeper = sleeper
        self._random_fn = random_fn
        self._now_fn = now_fn or (lambda: datetime.now(timezone.utc))

    def iter_patients(self, limit: int) -> Iterator[dict[str, Any]]:
        if limit < 1:
            raise ValueError("Patient limit must be positive")

        yield from self._iter_search(
            initial_url=f"{self.base_url}/Patient",
            initial_params={"_count": min(limit, self._patient_page_size)},
            expected_resource_type="Patient",
            limit=limit,
        )

    def iter_observations(
        self,
        patient_fhir_id: str,
    ) -> Iterator[dict[str, Any]]:
        if not isinstance(patient_fhir_id, str) or not FHIR_ID_PATTERN.fullmatch(
            patient_fhir_id
        ):
            raise ValueError("Patient FHIR ID is invalid")

        yield from self._iter_search(
            initial_url=f"{self.base_url}/Observation",
            initial_params={
                "subject": f"Patient/{patient_fhir_id}",
                "_count": self._observation_page_size,
            },
            expected_resource_type="Observation",
        )

    def _iter_search(
        self,
        *,
        initial_url: str,
        initial_params: Mapping[str, Any],
        expected_resource_type: str,
        limit: int | None = None,
    ) -> Iterator[dict[str, Any]]:
        url = initial_url
        params: Mapping[str, Any] | None = initial_params
        seen_urls: set[str] = set()
        yielded = 0

        while True:
            if url in seen_urls:
                raise FhirProtocolError(
                    f"FHIR {expected_resource_type} search repeated a next link"
                )
            seen_urls.add(url)

            bundle = self._get_search_bundle(
                url,
                params=params,
                context=f"{expected_resource_type} search",
            )
            entries = bundle.get("entry", [])
            if not isinstance(entries, list):
                raise FhirProtocolError(
                    f"FHIR {expected_resource_type} search returned invalid entries"
                )

            for bundle_entry in entries:
                resource = self._resource_from_entry(
                    bundle_entry,
                    expected_resource_type=expected_resource_type,
                )
                if resource is None:
                    continue

                yield resource
                yielded += 1
                if limit is not None and yielded >= limit:
                    return

            next_url = self._next_url(
                bundle,
                expected_resource_type=expected_resource_type,
            )
            if next_url is None:
                return
            self._validate_next_url(next_url, expected_resource_type)
            url = next_url
            params = None

    def _get_search_bundle(
        self,
        url: str,
        *,
        params: Mapping[str, Any] | None,
        context: str,
    ) -> dict[str, Any]:
        response = self._get_with_retry(url, params=params, context=context)
        try:
            payload = response.json()
        except ValueError:
            raise FhirProtocolError(f"FHIR {context} returned invalid JSON") from None

        if not isinstance(payload, dict):
            raise FhirProtocolError(f"FHIR {context} returned an invalid payload")
        if payload.get("resourceType") != "Bundle":
            raise FhirProtocolError(f"FHIR {context} did not return a Bundle")
        if payload.get("type") != "searchset":
            raise FhirProtocolError(f"FHIR {context} did not return a searchset")
        return payload

    def _get_with_retry(
        self,
        url: str,
        *,
        params: Mapping[str, Any] | None,
        context: str,
    ) -> Any:
        for attempt in range(self._max_retries + 1):
            try:
                response = self._session.get(
                    url,
                    params=params,
                    headers={"Accept": FHIR_JSON},
                    timeout=self._timeout,
                )
            except requests.RequestException:
                if attempt >= self._max_retries:
                    raise FhirClientError(
                        f"FHIR {context} failed after {attempt + 1} attempts"
                    ) from None
                self._sleeper(self._backoff_delay(attempt))
                continue

            status_code = response.status_code
            if status_code in TRANSIENT_STATUS_CODES:
                if attempt >= self._max_retries:
                    raise FhirClientError(
                        f"FHIR {context} failed with status {status_code} "
                        f"after {attempt + 1} attempts"
                    )
                delay = self._retry_after_delay(response.headers.get("Retry-After"))
                self._sleeper(
                    delay if delay is not None else self._backoff_delay(attempt)
                )
                continue

            if status_code < 200 or status_code >= 300:
                raise FhirClientError(
                    f"FHIR {context} failed with status {status_code}"
                )
            return response

        raise AssertionError("Retry loop exited unexpectedly")

    def _backoff_delay(self, attempt: int) -> float:
        exponential = self._backoff_factor * (2**attempt)
        jitter = self._random_fn() * self._backoff_factor
        return min(self._max_backoff, exponential + jitter)

    def _retry_after_delay(self, raw_value: str | None) -> float | None:
        if raw_value is None:
            return None
        try:
            seconds = float(raw_value)
        except (TypeError, ValueError):
            seconds = None
        if seconds is not None and math.isfinite(seconds) and seconds >= 0:
            return min(self._max_backoff, seconds)

        try:
            retry_at = parsedate_to_datetime(raw_value)
        except (TypeError, ValueError, OverflowError):
            return None
        if retry_at is None:
            return None
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=timezone.utc)

        now = self._now_fn()
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        delay = max(0.0, (retry_at - now).total_seconds())
        return min(self._max_backoff, delay)

    @staticmethod
    def _resource_from_entry(
        bundle_entry: Any,
        *,
        expected_resource_type: str,
    ) -> dict[str, Any] | None:
        if not isinstance(bundle_entry, dict):
            raise FhirProtocolError(
                f"FHIR {expected_resource_type} search returned an invalid entry"
            )

        resource = bundle_entry.get("resource")
        search = bundle_entry.get("search")
        if (
            isinstance(resource, dict)
            and resource.get("resourceType") == "OperationOutcome"
            and isinstance(search, dict)
            and search.get("mode") == "outcome"
        ):
            return None
        if not isinstance(resource, dict):
            raise FhirProtocolError(
                f"FHIR {expected_resource_type} search returned an invalid resource"
            )
        if resource.get("resourceType") != expected_resource_type:
            raise FhirProtocolError(
                f"FHIR {expected_resource_type} search returned a different resource type"
            )
        return resource

    @staticmethod
    def _next_url(
        bundle: Mapping[str, Any],
        *,
        expected_resource_type: str,
    ) -> str | None:
        links = bundle.get("link", [])
        if not isinstance(links, list):
            raise FhirProtocolError(
                f"FHIR {expected_resource_type} search returned invalid links"
            )

        next_urls = [
            link.get("url")
            for link in links
            if isinstance(link, dict) and link.get("relation") == "next"
        ]
        if len(next_urls) > 1 or (next_urls and not isinstance(next_urls[0], str)):
            raise FhirProtocolError(
                f"FHIR {expected_resource_type} search returned an invalid next link"
            )
        return next_urls[0] if next_urls else None

    def _validate_next_url(self, next_url: str, expected_resource_type: str) -> None:
        parsed_next_url = urlsplit(next_url)
        next_origin = (
            parsed_next_url.scheme.lower(),
            parsed_next_url.netloc.lower(),
        )
        if next_origin != self._origin:
            raise FhirProtocolError(
                f"FHIR {expected_resource_type} search returned a cross-origin next link"
            )
