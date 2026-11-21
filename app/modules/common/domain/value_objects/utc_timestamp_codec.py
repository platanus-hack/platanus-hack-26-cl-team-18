"""Pure UTC timestamp parsing/formatting codec.

Lives in the domain layer because it has zero application/infrastructure
dependencies — only stdlib and the domain ``UtcTimestamp`` value object — and is
needed by domain value objects (e.g. ``UtcTimestamp``-bearing snapshots) that codec timestamps as
part of their canonical, persisted serialization. Keeping it here lets the domain
stay self-contained without reaching up into the application layer.

The application-layer module ``app.modules.common.application.parsers.timestamps``
re-exports these symbols so the transport/application boundary can keep importing
from its established location without any behavioral change.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

from app.modules.common.domain.value_objects.utc_timestamp import UtcTimestamp

_ISO_8601_AWARE_RE = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2})"
    r"T(?P<time>\d{2}:\d{2}:\d{2})"
    r"(?:\.(?P<fraction>\d+))?"
    r"(?P<offset>Z|[+-]\d{2}:\d{2})$",
)


def parse_utc_datetime(
    value: str | datetime | UtcTimestamp,
    *,
    field_name: str = "timestamp",
) -> datetime:
    """Parse an API/application-layer instant and normalize it to a UTC-aware datetime.

    Accepts ISO-8601 strings with explicit offsets (including `Z`) so transport
    boundaries can stay flexible while the domain remains UTC-strict.
    """
    if isinstance(value, UtcTimestamp):
        return _normalize_to_utc_datetime(value.value, field_name=field_name)

    if isinstance(value, datetime):
        return _normalize_to_utc_datetime(value, field_name=field_name)

    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be str, datetime, or UtcTimestamp")

    if value != value.strip():
        raise ValueError(f"{field_name} must not include surrounding whitespace")

    match = _ISO_8601_AWARE_RE.fullmatch(value)
    if match is None:
        raise ValueError(f"{field_name} must be ISO-8601 with an explicit timezone offset")

    offset = match.group("offset")
    parsed = datetime.fromisoformat(
        f"{match.group('date')}T{match.group('time')}{'+00:00' if offset == 'Z' else offset}",
    )
    normalized = _normalize_to_utc_datetime(parsed, field_name=field_name)
    rounded_millis = _round_fraction_to_milliseconds(match.group("fraction"))
    return normalized + timedelta(milliseconds=rounded_millis)


def parse_persisted_utc_datetime(value: str, *, field_name: str = "timestamp") -> datetime:
    """Decode a stored instant without the public API's millisecond rounding.

    Persisted audit evidence must round-trip exactly, including microseconds.
    Keep the same explicit-offset syntax as the transport parser, but reject
    precision datetime cannot represent instead of silently truncating it.
    """
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be str")
    match = _ISO_8601_AWARE_RE.fullmatch(value)
    if match is None:
        raise ValueError(f"{field_name} must be ISO-8601 with an explicit timezone offset")
    fraction = match.group("fraction")
    if fraction is not None and len(fraction) > 6:
        raise ValueError(f"{field_name} must have at most microsecond precision")
    return datetime.fromisoformat(value).astimezone(UTC)


def parse_utc_timestamp(
    value: str | datetime | UtcTimestamp,
    *,
    field_name: str = "timestamp",
) -> UtcTimestamp:
    """Parse a boundary instant and return the strict domain UTC value object."""
    return UtcTimestamp.create(parse_utc_datetime(value, field_name=field_name))


def format_utc_datetime(value: datetime | UtcTimestamp, *, field_name: str = "timestamp") -> str:
    """Serialize an instant to canonical UTC ISO-8601 with millisecond precision."""
    normalized = parse_utc_datetime(value, field_name=field_name)
    return normalized.strftime("%Y-%m-%dT%H:%M:%S.%f")[:23] + "Z"


def _normalize_to_utc_datetime(value: datetime, *, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return _round_to_millis(value.astimezone(UTC))


def _round_to_millis(value: datetime) -> datetime:
    rounded_microseconds = ((value.microsecond + 500) // 1000) * 1000
    return value.replace(microsecond=0) + timedelta(microseconds=rounded_microseconds)


def parse_utc_only_datetime(
    value: str | datetime | UtcTimestamp,
    *,
    field_name: str = "timestamp",
) -> datetime:
    """Parse an instant and normalize to UTC, rejecting non-UTC timezone offsets.

    Accepts only ``Z`` or ``+00:00`` as the offset designator.
    """
    if isinstance(value, UtcTimestamp):
        return _normalize_to_utc_datetime(value.value, field_name=field_name)

    if isinstance(value, datetime):
        dt = _normalize_to_utc_datetime(value, field_name=field_name)
        if value.tzinfo is not None and value.utcoffset() != timedelta(0):
            raise ValueError(
                f"{field_name} must be in UTC (got offset {value.strftime('%z')}). "
                "Send timestamps with 'Z' or '+00:00' suffix.",
            )
        return dt

    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be str, datetime, or UtcTimestamp")

    if value != value.strip():
        raise ValueError(f"{field_name} must not include surrounding whitespace")

    match = _ISO_8601_AWARE_RE.fullmatch(value)
    if match is None:
        raise ValueError(f"{field_name} must be ISO-8601 with an explicit timezone offset")

    offset = match.group("offset")
    if offset not in ("Z", "+00:00"):
        raise ValueError(
            f"{field_name} must be in UTC (got '{offset}'). "
            "Send timestamps with 'Z' or '+00:00' suffix.",
        )

    parsed = datetime.fromisoformat(f"{match.group('date')}T{match.group('time')}+00:00")
    normalized = _normalize_to_utc_datetime(parsed, field_name=field_name)
    rounded_millis = _round_fraction_to_milliseconds(match.group("fraction"))
    return normalized + timedelta(milliseconds=rounded_millis)


def _round_fraction_to_milliseconds(fraction: str | None) -> int:
    if fraction is None:
        return 0

    numerator = int(fraction) * 1000
    denominator = 10 ** len(fraction)
    return int((numerator + (denominator // 2)) // denominator)
