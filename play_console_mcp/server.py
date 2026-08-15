"""FastMCP server exposing read-only Google Play Console report data.

Data sources:
- Play Developer Reporting API (vitals metric sets, error issues/reports,
  anomalies): https://developers.google.com/play/developer/reporting
- Play Console CSV exports in the developer's Cloud Storage bucket
  (installs, ratings, crashes, store performance, subscriptions, earnings).
"""

from __future__ import annotations

import argparse
import csv
import gzip
import io
import os
import re
from typing import Any

from fastmcp import FastMCP
from mcp.types import ToolAnnotations

from . import api

READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True)

mcp = FastMCP(
    name="google-play-console",
    instructions=(
        "Read-only report data from Google Play Console. Use "
        "search_accessible_apps to discover package names, "
        "query_metric_set for vitals time series (crash rate, ANR rate, "
        "slow start, etc.), search_error_issues/search_error_reports for "
        "crash clusters, list_anomalies for detected metric anomalies, and "
        "list_stats_reports/download_stats_report for the CSV exports "
        "(installs, ratings, store performance...)."
    ),
)

# Metric set name -> (default metrics, allowed extra info) taken from
# https://developers.google.com/play/developer/reporting/reference/rest
METRIC_SETS: dict[str, list[str]] = {
    "crashRateMetricSet": ["crashRate", "userPerceivedCrashRate", "distinctUsers"],
    "anrRateMetricSet": ["anrRate", "userPerceivedAnrRate", "distinctUsers"],
    "errorCountMetricSet": ["errorReportCount", "distinctUsers"],
    "excessiveWakeupRateMetricSet": ["excessiveWakeupRate", "distinctUsers"],
    "stuckBackgroundWakelockRateMetricSet": ["stuckBgWakelockRate", "distinctUsers"],
    "slowStartRateMetricSet": ["slowStartRate", "distinctUsers"],
    "slowRenderingRateMetricSet": [
        "slowRenderingRate20Fps",
        "slowRenderingRate30Fps",
        "distinctUsers",
    ],
    "lmkRateMetricSet": ["userPerceivedLmkRate", "distinctUsers"],
}

_PACKAGE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)+$")

# Daily aggregation in this API is anchored to America/Los_Angeles;
# hourly aggregation is anchored to UTC.
DAILY_TZ = "America/Los_Angeles"
HOURLY_TZ = "UTC"


def _app_name(package_name: str) -> str:
    if not _PACKAGE_RE.fullmatch(package_name):
        raise ValueError(f"Invalid package_name {package_name!r}: must look like com.example.app")
    return f"apps/{package_name}"


def _clamp(value: int, low: int, high: int) -> int:
    return min(max(value, low), high)


AGGREGATION_PERIODS = {"DAILY", "HOURLY", "FULL_RANGE"}


def _check_range(start_date: str, end_date: str) -> None:
    start = api.date_time(start_date, "UTC")
    end = api.date_time(end_date, "UTC")
    if (start["year"], start["month"], start["day"]) > (end["year"], end["month"], end["day"]):
        raise ValueError(f"start_date {start_date} is after end_date {end_date}")


@mcp.tool(annotations=READ_ONLY)
def search_accessible_apps(page_size: int = 100, page_token: str | None = None) -> dict[str, Any]:
    """List the Play Console apps the configured credentials can access.

    Use this first to discover valid package names for the other tools.
    Returns app name (apps/<package>), display name and package name.
    """
    params: dict[str, Any] = {"pageSize": _clamp(page_size, 1, 1000)}
    if page_token:
        params["pageToken"] = page_token
    return api.reporting_get("apps:search", params)


@mcp.tool(annotations=READ_ONLY)
def list_metric_sets() -> dict[str, Any]:
    """Describe the available vitals metric sets and their default metrics.

    Helps choose the `metric_set` and `metrics` arguments of query_metric_set.
    """
    return {
        "metricSets": METRIC_SETS,
        "notes": (
            "Common dimensions: versionCode, apiLevel, deviceModel, deviceBrand, "
            "deviceType, countryCode, deviceRamBucket, deviceSocMake, deviceSocModel. "
            "errorCountMetricSet also supports reportType and issueId. "
            "Rolling-average metrics such as crashRate7dUserWeighted and "
            "crashRate28dUserWeighted exist for crash/ANR rate sets."
        ),
    }


@mcp.tool(annotations=READ_ONLY)
def get_metric_set_freshness(package_name: str, metric_set: str) -> dict[str, Any]:
    """Get freshness info for a metric set: which aggregation periods exist
    and the latest end time with data. Call this when a query returns no rows
    to check whether data for the requested range is available yet.

    Args:
        package_name: App package name, e.g. "com.example.app".
        metric_set: One of the keys returned by list_metric_sets.
    """
    if metric_set not in METRIC_SETS:
        raise ValueError(f"Unknown metric set {metric_set!r}. Valid: {sorted(METRIC_SETS)}")
    return api.reporting_get(f"{_app_name(package_name)}/{metric_set}")


@mcp.tool(annotations=READ_ONLY)
def query_metric_set(
    package_name: str,
    metric_set: str,
    start_date: str,
    end_date: str,
    metrics: list[str] | None = None,
    dimensions: list[str] | None = None,
    aggregation_period: str = "DAILY",
    filter: str | None = None,
    page_size: int = 1000,
    page_token: str | None = None,
) -> dict[str, Any]:
    """Query a vitals metric set as a time series (the core reporting tool).

    Examples: daily crash rate per version, ANR rate by country, error report
    counts per issue.

    Args:
        package_name: App package name, e.g. "com.example.app".
        metric_set: One of list_metric_sets (e.g. "crashRateMetricSet").
        start_date / end_date: Inclusive range, "YYYY-MM-DD". Daily data is
            anchored to America/Los_Angeles days.
        metrics: Metric names to return; defaults to the set's common metrics.
        dimensions: Optional dimensions to break the rows down by
            (e.g. ["versionCode"], ["countryCode"]).
        aggregation_period: "DAILY", "HOURLY" or "FULL_RANGE". HOURLY is only
            supported by some sets and is anchored to UTC.
        filter: Optional AIP-160 filter over dimensions, e.g.
            "versionCode = 1234" or "countryCode IN ('BR', 'US')".
        page_size: Max rows per page (API max 100000).
        page_token: Token from a previous response to fetch the next page.

    Returns rows with startTime, dimension values and decimal metric values,
    plus nextPageToken when there are more rows.
    """
    if metric_set not in METRIC_SETS:
        raise ValueError(f"Unknown metric set {metric_set!r}. Valid: {sorted(METRIC_SETS)}")
    if aggregation_period not in AGGREGATION_PERIODS:
        raise ValueError(
            f"Invalid aggregation_period {aggregation_period!r}. "
            f"Valid: {sorted(AGGREGATION_PERIODS)}"
        )
    _check_range(start_date, end_date)
    time_zone = HOURLY_TZ if aggregation_period == "HOURLY" else DAILY_TZ
    body: dict[str, Any] = {
        "timelineSpec": {
            "aggregationPeriod": aggregation_period,
            "startTime": api.date_time(start_date, time_zone),
            # The API's endTime is exclusive; shift so end_date is inclusive.
            "endTime": api.date_time(api.next_day(end_date), time_zone),
        },
        "metrics": metrics or METRIC_SETS[metric_set],
        "pageSize": _clamp(page_size, 1, 100_000),
    }
    if dimensions:
        body["dimensions"] = dimensions
    if filter:
        body["filter"] = filter
    if page_token:
        body["pageToken"] = page_token
    return api.reporting_post(f"{_app_name(package_name)}/{metric_set}:query", body)


@mcp.tool(annotations=READ_ONLY)
def search_error_issues(
    package_name: str,
    start_date: str,
    end_date: str,
    filter: str | None = None,
    order_by: str | None = None,
    sample_error_report_limit: int = 0,
    page_size: int = 50,
    page_token: str | None = None,
) -> dict[str, Any]:
    """Search crash/ANR issue clusters (grouped errors, like the Play Console
    "Crashes and ANRs" page).

    Args:
        package_name: App package name.
        start_date / end_date: "YYYY-MM-DD" range (UTC days).
        filter: Optional AIP-160 filter, e.g. "errorIssueType = CRASH",
            "errorIssueType = ANR", "apiLevel = 33", "versionCode = 1234",
            or "errorIssueType = CRASH AND versionCode = 1234".
        order_by: e.g. "errorReportCount desc" or "distinctUsers desc".
        sample_error_report_limit: If > 0, include up to N sample report names
            per issue.
        page_size: Issues per page.
        page_token: Pagination token from a previous call.
    """
    _check_range(start_date, end_date)
    params: dict[str, Any] = {"pageSize": _clamp(page_size, 1, 1000)}
    params.update(api.flatten_date_time("interval.startTime", start_date, "UTC"))
    params.update(api.flatten_date_time("interval.endTime", api.next_day(end_date), "UTC"))
    if filter:
        params["filter"] = filter
    if order_by:
        params["orderBy"] = order_by
    if sample_error_report_limit:
        params["sampleErrorReportLimit"] = sample_error_report_limit
    if page_token:
        params["pageToken"] = page_token
    return api.reporting_get(f"{_app_name(package_name)}/errorIssues:search", params)


@mcp.tool(annotations=READ_ONLY)
def search_error_reports(
    package_name: str,
    start_date: str,
    end_date: str,
    filter: str | None = None,
    page_size: int = 50,
    page_token: str | None = None,
) -> dict[str, Any]:
    """Search individual de-identified error reports (single crash/ANR
    occurrences with stack traces).

    Args:
        package_name: App package name.
        start_date / end_date: "YYYY-MM-DD" range (UTC days).
        filter: Optional AIP-160 filter, e.g. "errorIssueId = <id>" to fetch
            reports of one issue from search_error_issues, or
            "errorReportType = CRASH".
        page_size: Reports per page.
        page_token: Pagination token from a previous call.
    """
    _check_range(start_date, end_date)
    params: dict[str, Any] = {"pageSize": _clamp(page_size, 1, 1000)}
    params.update(api.flatten_date_time("interval.startTime", start_date, "UTC"))
    params.update(api.flatten_date_time("interval.endTime", api.next_day(end_date), "UTC"))
    if filter:
        params["filter"] = filter
    if page_token:
        params["pageToken"] = page_token
    return api.reporting_get(f"{_app_name(package_name)}/errorReports:search", params)


@mcp.tool(annotations=READ_ONLY)
def list_anomalies(
    package_name: str,
    filter: str | None = None,
    page_size: int = 50,
    page_token: str | None = None,
) -> dict[str, Any]:
    """List anomalies Google Play detected in the app's metrics (e.g. a spike
    in crash rate).

    Args:
        package_name: App package name.
        filter: Optional filter, e.g. "activeBetween(\"2026-08-01T00:00:00Z\", UNBOUNDED)".
        page_size: Anomalies per page.
        page_token: Pagination token from a previous call.
    """
    params: dict[str, Any] = {"pageSize": _clamp(page_size, 1, 1000)}
    if filter:
        params["filter"] = filter
    if page_token:
        params["pageToken"] = page_token
    return api.reporting_get(f"{_app_name(package_name)}/anomalies", params)


# ---------------------------------------------------------------------------
# CSV stats exports (Cloud Storage bucket pubsite_prod_*)
# ---------------------------------------------------------------------------

BUCKET_ENV = "PLAY_CONSOLE_GCS_BUCKET"


def _bucket(bucket: str | None) -> str:
    resolved = bucket or os.environ.get(BUCKET_ENV)
    if not resolved:
        raise ValueError(
            "No bucket given. Pass `bucket` or set the "
            f"{BUCKET_ENV} env var (looks like 'pubsite_prod_rev_01234567890987654321', "
            "shown in Play Console under Download reports > Copy Cloud Storage URI)."
        )
    # Accept the full URI Play Console copies, e.g.
    # "gs://pubsite_prod_rev_0123/stats/installs/": keep only the bucket name.
    return resolved.removeprefix("gs://").strip("/").split("/", 1)[0]


@mcp.tool(annotations=READ_ONLY)
def list_stats_reports(
    prefix: str = "stats/",
    bucket: str | None = None,
    page_size: int = 100,
    page_token: str | None = None,
) -> dict[str, Any]:
    """List CSV report files exported by Play Console to Cloud Storage.

    These cover the reports not available in the Reporting API: installs,
    ratings, crashes (legacy), store performance, subscriptions, earnings.

    Args:
        prefix: Object prefix to filter by. Useful values: "stats/installs/",
            "stats/ratings/", "stats/crashes/", "stats/store_performance/",
            "financial-stats/", "earnings/", "reviews/", or "" for everything.
        bucket: Report bucket (defaults to $PLAY_CONSOLE_GCS_BUCKET).
        page_size: Files per page (1-1000). Prefer a narrow prefix over a
            large page.
        page_token: Pagination token from a previous call.

    Returns object names like
    "stats/installs/installs_com.example.app_202608_country.csv".
    """
    result = api.storage_list(_bucket(bucket), prefix, page_token, _clamp(page_size, 1, 1000))
    return {
        "files": [
            {"name": item["name"], "size": item.get("size"), "updated": item.get("updated")}
            for item in result.get("items", [])
        ],
        "nextPageToken": result.get("nextPageToken"),
    }


def _decode_report(payload: bytes) -> str:
    # Play Console exports are commonly UTF-16 with BOM; some newer report
    # families (reviews, earnings) are UTF-8. Detect the BOM before decoding.
    if payload.startswith((b"\xff\xfe", b"\xfe\xff")):
        return payload.decode("utf-16")
    return payload.decode("utf-8-sig")


@mcp.tool(annotations=READ_ONLY)
def download_stats_report(
    object_name: str,
    bucket: str | None = None,
    max_rows: int = 1000,
    max_bytes: int = 5_000_000,
) -> dict[str, Any]:
    """Download one CSV (or CSV.GZ) report file from the Play Console bucket
    and return it parsed as JSON rows. Get valid object names from
    list_stats_reports.

    Args:
        object_name: Full object name, e.g.
            "stats/installs/installs_com.example.app_202608_overview.csv".
        bucket: Report bucket (defaults to $PLAY_CONSOLE_GCS_BUCKET).
        max_rows: Cap on returned rows (1-5000); `truncated` flags overflow.
        max_bytes: Safety cap on file size, checked before downloading.
    """
    if not object_name or object_name.startswith("/") or ".." in object_name.split("/"):
        raise ValueError("object_name must be a relative object path")
    max_rows = _clamp(max_rows, 1, 5000)
    max_bytes = _clamp(max_bytes, 1024, 10_000_000)
    resolved_bucket = _bucket(bucket)
    size = int(api.storage_metadata(resolved_bucket, object_name).get("size", 0))
    if size > max_bytes:
        raise ValueError(
            f"File is {size} bytes, above max_bytes={max_bytes}. "
            "Raise max_bytes (up to 10MB) or pick a narrower report file."
        )
    payload = api.storage_download(resolved_bucket, object_name)
    if len(payload) > max_bytes:
        raise ValueError(f"Downloaded report exceeds max_bytes ({max_bytes}).")
    if object_name.endswith(".gz"):
        with gzip.GzipFile(fileobj=io.BytesIO(payload)) as compressed:
            payload = compressed.read(max_bytes + 1)
        if len(payload) > max_bytes:
            raise ValueError(f"Decompressed report exceeds max_bytes ({max_bytes}).")
    reader = csv.DictReader(io.StringIO(_decode_report(payload)))
    rows: list[dict[str, Any]] = []
    truncated = False
    for index, row in enumerate(reader):
        if index >= max_rows:
            truncated = True
            break
        rows.append(dict(row))
    return {
        "object": object_name,
        "columns": reader.fieldnames or [],
        "rows": rows,
        "truncated": truncated,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Google Play Console MCP server")
    parser.add_argument(
        "--transport",
        choices=["stdio", "streamable-http"],
        default="stdio",
        help="Transport to use (default: stdio)",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    if args.transport == "stdio":
        mcp.run()
    else:
        mcp.run(transport="streamable-http", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
