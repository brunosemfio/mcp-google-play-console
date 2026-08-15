"""Thin helpers over the Google REST APIs used by the tools."""

from __future__ import annotations

from typing import Any

from .auth import get_session

REPORTING_BASE = "https://playdeveloperreporting.googleapis.com/v1beta1"
STORAGE_BASE = "https://storage.googleapis.com/storage/v1"
TIMEOUT = 60


class PlayConsoleApiError(RuntimeError):
    pass


def _check(response) -> dict[str, Any]:
    if response.status_code >= 400:
        try:
            detail = response.json().get("error", {})
            message = detail.get("message", response.text)
        except ValueError:
            message = response.text
        raise PlayConsoleApiError(f"HTTP {response.status_code}: {message}")
    if not response.content:
        return {}
    return response.json()


def reporting_get(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    response = get_session().get(f"{REPORTING_BASE}/{path}", params=params or {}, timeout=TIMEOUT)
    return _check(response)


def reporting_post(path: str, body: dict[str, Any]) -> dict[str, Any]:
    response = get_session().post(f"{REPORTING_BASE}/{path}", json=body, timeout=TIMEOUT)
    return _check(response)


def storage_list(
    bucket: str, prefix: str, page_token: str | None = None, max_results: int = 100
) -> dict[str, Any]:
    params: dict[str, Any] = {"prefix": prefix, "maxResults": max_results}
    if page_token:
        params["pageToken"] = page_token
    response = get_session().get(f"{STORAGE_BASE}/b/{bucket}/o", params=params, timeout=TIMEOUT)
    return _check(response)


def _object_url(bucket: str, object_name: str) -> str:
    from urllib.parse import quote

    return f"{STORAGE_BASE}/b/{bucket}/o/{quote(object_name, safe='')}"


def storage_metadata(bucket: str, object_name: str) -> dict[str, Any]:
    response = get_session().get(
        _object_url(bucket, object_name), params={"fields": "size"}, timeout=TIMEOUT
    )
    return _check(response)


def storage_download(bucket: str, object_name: str) -> bytes:
    response = get_session().get(
        _object_url(bucket, object_name), params={"alt": "media"}, timeout=TIMEOUT
    )
    if response.status_code >= 400:
        _check(response)
    return response.content


def date_time(date: str, time_zone: str) -> dict[str, Any]:
    """Convert an ISO date string (YYYY-MM-DD) into the API's DateTime shape."""
    try:
        year, month, day = (int(part) for part in date.split("-"))
    except ValueError as exc:
        raise PlayConsoleApiError(
            f"Invalid date {date!r}: expected YYYY-MM-DD"
        ) from exc
    return {
        "year": year,
        "month": month,
        "day": day,
        "timeZone": {"id": time_zone},
    }


def next_day(date: str) -> str:
    """Day after an ISO date. The reporting API's end times are exclusive;
    the MCP tools take inclusive end dates and shift them with this."""
    from datetime import date as date_type
    from datetime import timedelta

    dt = date_time(date, "UTC")
    return str(date_type(dt["year"], dt["month"], dt["day"]) + timedelta(days=1))


def flatten_date_time(prefix: str, date: str, time_zone: str) -> dict[str, Any]:
    """Same as date_time but as dotted query params for GET endpoints."""
    dt = date_time(date, time_zone)
    return {
        f"{prefix}.year": dt["year"],
        f"{prefix}.month": dt["month"],
        f"{prefix}.day": dt["day"],
        f"{prefix}.timeZone.id": time_zone,
    }
