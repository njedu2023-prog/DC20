from __future__ import annotations

from typing import Any, Iterable, Sequence


RT_MIN_CODE_FIELDS = ("ts_code", "code")
RT_MIN_CANONICAL_FIELDS = (
    "ts_code",
    "freq",
    "time",
    "open",
    "close",
    "high",
    "low",
    "vol",
    "amount",
)
RT_MIN_VALUE_FIELDS = RT_MIN_CANONICAL_FIELDS[1:]
# Ask Tushare for its native rt_min_daily response surface.  The endpoint's
# documented request field is ``ts_code`` while the live result can identify
# the same column as ``code``.  Projecting ``ts_code`` on the wire can therefore
# turn a valid native response into an ambiguous schema.  Callers must request
# no projection and then validate the exact native surface below.
RT_MIN_WIRE_FIELDS: tuple[str, ...] = ()


class RTMinContractError(RuntimeError):
    """The realtime-minute response cannot be safely attributed to its request."""

    def __init__(self, message: str, *, reason: str, row_count: int) -> None:
        super().__init__(message)
        self.reason = str(reason)
        self.row_count = max(0, int(row_count))


def _required_text(value: object) -> str:
    return str(value if value is not None else "").strip()


def validate_rt_min_response(
    fields: Iterable[object],
    rows: Iterable[Sequence[Any]],
    *,
    expected_code: str,
    expected_freq: str = "1MIN",
) -> tuple[tuple[str, ...], list[list[Any]]]:
    """Validate and canonicalize one ``rt_min_daily`` response.

    Tushare documents ``ts_code`` as the request field while its live result can
    use ``code`` as the identity header.  Exactly one of those headers is
    accepted.  Every returned row must carry the requested identity and
    frequency before callers may persist it.
    """

    names = list(fields)
    response_rows = list(rows)
    row_count = len(response_rows)
    if not response_rows:
        return RT_MIN_CANONICAL_FIELDS, []
    if (
        any(
            type(field) is not str
            or not field
            or field != field.strip()
            for field in names
        )
        or len(set(names)) != len(names)
    ):
        raise RTMinContractError(
            "rt_min_daily: response fields are invalid or duplicated",
            reason="schema",
            row_count=row_count,
        )

    identity_fields = [field for field in RT_MIN_CODE_FIELDS if field in names]
    if len(identity_fields) != 1:
        raise RTMinContractError(
            "rt_min_daily: response identity field is missing or ambiguous",
            reason="schema",
            row_count=row_count,
        )
    identity_field = identity_fields[0]
    required_fields = (identity_field, *RT_MIN_VALUE_FIELDS)
    if set(names) != set(required_fields) or len(names) != len(required_fields):
        raise RTMinContractError(
            "rt_min_daily: response fields differ from the exact native contract",
            reason="schema",
            row_count=row_count,
        )

    expected_identity = _required_text(expected_code).upper()
    expected_frequency = _required_text(expected_freq).upper()
    if not expected_identity or not expected_frequency:
        raise RTMinContractError(
            "rt_min_daily: expected identity or frequency is empty",
            reason="schema",
            row_count=row_count,
        )

    indexes = {field: names.index(field) for field in required_fields}
    canonical_rows: list[list[Any]] = []
    for row in response_rows:
        if not isinstance(row, (list, tuple)) or len(row) != len(names):
            raise RTMinContractError(
                "rt_min_daily: row width differs from response fields",
                reason="schema",
                row_count=row_count,
            )
        if any(not _required_text(row[indexes[field]]) for field in required_fields):
            raise RTMinContractError(
                "rt_min_daily: one or more rows are incomplete",
                reason="schema",
                row_count=row_count,
            )
        actual_identity = _required_text(row[indexes[identity_field]]).upper()
        if actual_identity != expected_identity:
            raise RTMinContractError(
                "rt_min_daily: returned code differs from request",
                reason="identity",
                row_count=row_count,
            )
        actual_frequency = _required_text(row[indexes["freq"]]).upper()
        if actual_frequency != expected_frequency:
            raise RTMinContractError(
                "rt_min_daily: returned frequency differs from request",
                reason="frequency",
                row_count=row_count,
            )
        canonical_rows.append(
            [
                actual_identity,
                actual_frequency,
                *[row[indexes[field]] for field in RT_MIN_VALUE_FIELDS[1:]],
            ]
        )

    return RT_MIN_CANONICAL_FIELDS, canonical_rows


__all__ = [
    "RT_MIN_CANONICAL_FIELDS",
    "RT_MIN_CODE_FIELDS",
    "RT_MIN_VALUE_FIELDS",
    "RT_MIN_WIRE_FIELDS",
    "RTMinContractError",
    "validate_rt_min_response",
]
