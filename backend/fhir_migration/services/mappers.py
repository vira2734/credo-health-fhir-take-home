"""Null-safe FHIR R4 Patient and Observation transformation helpers."""

from collections.abc import Mapping
from copy import deepcopy
from datetime import date, datetime
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import re
from typing import Any

from django.utils.dateparse import parse_datetime
from django.utils.timezone import is_aware

FHIR_ID_PATTERN = re.compile(r"^[A-Za-z0-9\-.]{1,64}$")
FHIR_DATE_PATTERN = re.compile(
    r"^(?P<year>[0-9]{4})"
    r"(?:-(?P<month>0[1-9]|1[0-2])"
    r"(?:-(?P<day>0[1-9]|[12][0-9]|3[01]))?)?$"
)
FHIR_INSTANT_PATTERN = re.compile(
    r"^[0-9]{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12][0-9]|3[01])"
    r"T(?:[01][0-9]|2[0-3]):[0-5][0-9]:(?:[0-5][0-9]|60)"
    r"(?:\.[0-9]+)?"
    r"(?:Z|[+-](?:(?:0[0-9]|1[0-3]):[0-5][0-9]|14:00))$"
)

OBSERVATION_VALUE_FIELDS = (
    "valueQuantity",
    "valueCodeableConcept",
    "valueString",
    "valueBoolean",
    "valueInteger",
    "valueRange",
    "valueRatio",
    "valueSampledData",
    "valueTime",
    "valueDateTime",
    "valuePeriod",
)
OBSERVATION_EFFECTIVE_FIELDS = (
    "effectiveDateTime",
    "effectivePeriod",
    "effectiveTiming",
    "effectiveInstant",
)


class FhirMappingError(ValueError):
    """Sanitized validation failure for one source resource."""


@dataclass(frozen=True)
class MappedObservation:
    patient_fhir_id: str
    attributes: dict[str, Any]


def map_patient(
    resource: Mapping[str, Any],
    source_system: str,
) -> dict[str, Any]:
    """Map a FHIR Patient without choosing canonical contact information."""

    fhir_id = _validate_resource_identity(resource, "Patient")
    meta = _optional_mapping(resource, "meta", "Patient")
    names = _optional_list(resource, "name", "Patient")
    identifiers = _optional_list(resource, "identifier", "Patient")
    telecom = _optional_list(resource, "telecom", "Patient")
    addresses = _optional_list(resource, "address", "Patient")
    communications = _optional_list(resource, "communication", "Patient")

    birth_date = resource.get("birthDate")
    if birth_date is not None:
        _validate_fhir_date(birth_date, "Patient birthDate")

    active = resource.get("active")
    if active is not None and not isinstance(active, bool):
        raise FhirMappingError("FHIR Patient active must be a boolean")

    return {
        "source_system": _validate_source_system(source_system),
        "fhir_id": fhir_id,
        "source_version_id": _optional_string(
            meta,
            "versionId",
            "Patient meta.versionId",
        ),
        "source_last_updated": _optional_instant(
            meta.get("lastUpdated"),
            "Patient meta.lastUpdated",
        ),
        "names": names,
        "display_name": _patient_display_name(names),
        "gender": _optional_string(resource, "gender", "Patient gender"),
        "birth_date": birth_date,
        "active": active,
        "identifiers": identifiers,
        "telecom": telecom,
        "addresses": addresses,
        "communications": communications,
        "raw_resource": deepcopy(dict(resource)),
    }


def map_observation(
    resource: Mapping[str, Any],
    source_system: str,
) -> MappedObservation:
    """Map a FHIR Observation while preserving its choice fields."""

    fhir_id = _validate_resource_identity(resource, "Observation")
    patient_fhir_id = _patient_reference(resource)
    meta = _optional_mapping(resource, "meta", "Observation")
    categories = _optional_list(resource, "category", "Observation")
    code = _optional_mapping(resource, "code", "Observation")
    code_codings = _optional_list(code, "coding", "Observation code")
    code_text = _optional_string(code, "text", "Observation code.text")

    value_type, value = _choice_value(
        resource,
        OBSERVATION_VALUE_FIELDS,
        "Observation value",
    )
    data_absent_reason = _optional_mapping_or_none(
        resource,
        "dataAbsentReason",
        "Observation",
    )
    if value_type and data_absent_reason is not None:
        raise FhirMappingError(
            "FHIR Observation cannot contain value and dataAbsentReason"
        )

    value_numeric, value_text, value_unit = _value_projections(
        value_type,
        value,
    )

    effective_type, effective = _choice_value(
        resource,
        OBSERVATION_EFFECTIVE_FIELDS,
        "Observation effective",
    )
    effective_at = _effective_timestamp(effective_type, effective)

    attributes = {
        "source_system": _validate_source_system(source_system),
        "fhir_id": fhir_id,
        "source_version_id": _optional_string(
            meta,
            "versionId",
            "Observation meta.versionId",
        ),
        "source_last_updated": _optional_instant(
            meta.get("lastUpdated"),
            "Observation meta.lastUpdated",
        ),
        "status": _optional_string(resource, "status", "Observation status"),
        "categories": categories,
        "code_codings": code_codings,
        "code_text": code_text,
        "display_label": _observation_display_label(code_text, code_codings),
        "value_type": value_type,
        "value": value,
        "value_numeric": value_numeric,
        "value_text": value_text,
        "value_unit": value_unit,
        "data_absent_reason": data_absent_reason,
        "components": _optional_list(resource, "component", "Observation"),
        "reference_ranges": _optional_list(
            resource,
            "referenceRange",
            "Observation",
        ),
        "effective_type": effective_type,
        "effective": effective,
        "effective_at": effective_at,
        "issued": _optional_instant(resource.get("issued"), "Observation issued"),
        "raw_resource": deepcopy(dict(resource)),
    }
    return MappedObservation(
        patient_fhir_id=patient_fhir_id,
        attributes=attributes,
    )


def _validate_resource_identity(
    resource: Mapping[str, Any],
    expected_resource_type: str,
) -> str:
    if not isinstance(resource, Mapping):
        raise FhirMappingError(f"FHIR {expected_resource_type} must be an object")
    if resource.get("resourceType") != expected_resource_type:
        raise FhirMappingError(
            f"FHIR resource is not an {expected_resource_type}"
        )

    fhir_id = resource.get("id")
    if not isinstance(fhir_id, str) or not FHIR_ID_PATTERN.fullmatch(fhir_id):
        raise FhirMappingError(f"FHIR {expected_resource_type} id is invalid")
    return fhir_id


def _validate_source_system(source_system: str) -> str:
    if not isinstance(source_system, str) or not source_system.strip():
        raise FhirMappingError("FHIR source system is invalid")
    return source_system.rstrip("/")


def _optional_mapping(
    container: Mapping[str, Any],
    field: str,
    context: str,
) -> Mapping[str, Any]:
    value = container.get(field)
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise FhirMappingError(f"FHIR {context} {field} must be an object")
    return value


def _optional_mapping_or_none(
    container: Mapping[str, Any],
    field: str,
    context: str,
) -> dict[str, Any] | None:
    value = container.get(field)
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise FhirMappingError(f"FHIR {context} {field} must be an object")
    return deepcopy(dict(value))


def _optional_list(
    container: Mapping[str, Any],
    field: str,
    context: str,
) -> list[Any]:
    value = container.get(field)
    if value is None:
        return []
    if not isinstance(value, list):
        raise FhirMappingError(f"FHIR {context} {field} must be a list")
    return deepcopy(value)


def _optional_string(
    container: Mapping[str, Any],
    field: str,
    context: str,
) -> str:
    value = container.get(field)
    if value is None:
        return ""
    if not isinstance(value, str):
        raise FhirMappingError(f"FHIR {context} must be a string")
    return value


def _validate_fhir_date(value: Any, context: str) -> None:
    if not isinstance(value, str):
        raise FhirMappingError(f"FHIR {context} must be a date string")

    matched = FHIR_DATE_PATTERN.fullmatch(value)
    if matched is None:
        raise FhirMappingError(f"FHIR {context} is invalid")
    year = int(matched.group("year"))
    month = int(matched.group("month") or 1)
    day = int(matched.group("day") or 1)
    if year == 0:
        raise FhirMappingError(f"FHIR {context} is invalid")
    try:
        date(year, month, day)
    except ValueError:
        raise FhirMappingError(f"FHIR {context} is invalid") from None


def _optional_instant(value: Any, context: str) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise FhirMappingError(f"FHIR {context} must be a timestamp string")

    if FHIR_INSTANT_PATTERN.fullmatch(value) is None:
        raise FhirMappingError(f"FHIR {context} is invalid")

    try:
        parsed = parse_datetime(value)
    except (ValueError, OverflowError):
        raise FhirMappingError(f"FHIR {context} is invalid") from None
    if parsed is None or not is_aware(parsed):
        raise FhirMappingError(f"FHIR {context} is invalid")
    return parsed


def _patient_display_name(names: list[Any]) -> str:
    if not names:
        return ""
    first_name = names[0]
    if not isinstance(first_name, Mapping):
        raise FhirMappingError("FHIR Patient name must contain objects")

    text = first_name.get("text")
    if text is not None:
        if not isinstance(text, str):
            raise FhirMappingError("FHIR Patient name.text must be a string")
        if text.strip():
            return text.strip()

    given = first_name.get("given") or []
    if not isinstance(given, list) or any(not isinstance(item, str) for item in given):
        raise FhirMappingError("FHIR Patient name.given must be a list of strings")
    family = first_name.get("family") or ""
    if not isinstance(family, str):
        raise FhirMappingError("FHIR Patient name.family must be a string")

    parts = [item.strip() for item in given if item.strip()]
    if family.strip():
        parts.append(family.strip())
    return " ".join(parts)


def _patient_reference(resource: Mapping[str, Any]) -> str:
    subject = resource.get("subject")
    if not isinstance(subject, Mapping):
        raise FhirMappingError("FHIR Observation subject must reference a Patient")
    reference = subject.get("reference")
    if not isinstance(reference, str):
        raise FhirMappingError("FHIR Observation subject must reference a Patient")

    matched = re.fullmatch(r"Patient/([A-Za-z0-9\-.]{1,64})", reference)
    if matched is None:
        raise FhirMappingError("FHIR Observation subject must reference a Patient")
    return matched.group(1)


def _choice_value(
    resource: Mapping[str, Any],
    fields: tuple[str, ...],
    context: str,
) -> tuple[str, Any]:
    present_fields = [field for field in fields if field in resource]
    if len(present_fields) > 1:
        raise FhirMappingError(f"FHIR {context} contains multiple choices")
    if not present_fields:
        return "", None

    field = present_fields[0]
    value = resource[field]
    if value is None:
        raise FhirMappingError(f"FHIR {context} choice is empty")
    return field, deepcopy(value)


def _value_projections(
    value_type: str,
    value: Any,
) -> tuple[Decimal | None, str, str]:
    value_numeric = None
    value_text = ""
    value_unit = ""

    if value_type == "valueQuantity":
        if not isinstance(value, Mapping):
            raise FhirMappingError("FHIR Observation valueQuantity must be an object")
        raw_number = value.get("value")
        if raw_number is not None:
            if isinstance(raw_number, bool) or not isinstance(
                raw_number,
                (int, float, Decimal),
            ):
                raise FhirMappingError(
                    "FHIR Observation valueQuantity.value is invalid"
                )
            try:
                value_numeric = Decimal(str(raw_number))
            except (InvalidOperation, TypeError, ValueError):
                raise FhirMappingError(
                    "FHIR Observation valueQuantity.value is invalid"
                ) from None
            if not value_numeric.is_finite():
                raise FhirMappingError(
                    "FHIR Observation valueQuantity.value is invalid"
                )
        unit = value.get("unit")
        if unit is not None:
            if not isinstance(unit, str):
                raise FhirMappingError(
                    "FHIR Observation valueQuantity.unit must be a string"
                )
            value_unit = unit
    elif value_type == "valueString":
        if not isinstance(value, str):
            raise FhirMappingError("FHIR Observation valueString must be a string")
        value_text = value
    elif value_type == "valueBoolean":
        if not isinstance(value, bool):
            raise FhirMappingError("FHIR Observation valueBoolean must be a boolean")
    elif value_type == "valueInteger":
        if not isinstance(value, int) or isinstance(value, bool):
            raise FhirMappingError("FHIR Observation valueInteger must be an integer")
        value_numeric = Decimal(value)
    elif value_type in {
        "valueCodeableConcept",
        "valueRange",
        "valueRatio",
        "valueSampledData",
        "valuePeriod",
    }:
        if not isinstance(value, Mapping):
            raise FhirMappingError(f"FHIR Observation {value_type} must be an object")
    elif value_type in {"valueTime", "valueDateTime"}:
        if not isinstance(value, str):
            raise FhirMappingError(f"FHIR Observation {value_type} must be a string")
        if value_type == "valueDateTime":
            _fhir_datetime_timestamp(value, "Observation valueDateTime")

    return value_numeric, value_text, value_unit


def _effective_timestamp(
    effective_type: str,
    effective: Any,
) -> datetime | None:
    if not effective_type:
        return None
    if effective_type == "effectiveDateTime":
        if not isinstance(effective, str):
            raise FhirMappingError(
                "FHIR Observation effectiveDateTime must be a string"
            )
        return _fhir_datetime_timestamp(
            effective,
            "Observation effectiveDateTime",
        )
    if effective_type == "effectiveInstant":
        return _optional_instant(effective, "Observation effectiveInstant")
    if not isinstance(effective, Mapping):
        raise FhirMappingError(f"FHIR Observation {effective_type} must be an object")
    return None


def _fhir_datetime_timestamp(value: str, context: str) -> datetime | None:
    if "T" not in value:
        _validate_fhir_date(value, context)
        return None

    if FHIR_INSTANT_PATTERN.fullmatch(value) is None:
        raise FhirMappingError(f"FHIR {context} is invalid")

    try:
        parsed = parse_datetime(value)
    except (ValueError, OverflowError):
        raise FhirMappingError(f"FHIR {context} is invalid") from None
    if parsed is None or not is_aware(parsed):
        raise FhirMappingError(f"FHIR {context} is invalid")
    return parsed


def _observation_display_label(
    code_text: str,
    code_codings: list[Any],
) -> str:
    if code_text.strip():
        return code_text.strip()
    if not code_codings:
        return ""

    first_coding = code_codings[0]
    if not isinstance(first_coding, Mapping):
        raise FhirMappingError("FHIR Observation code.coding must contain objects")
    for field in ("display", "code"):
        value = first_coding.get(field)
        if value is not None and not isinstance(value, str):
            raise FhirMappingError(
                f"FHIR Observation code.coding.{field} must be a string"
            )
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""
