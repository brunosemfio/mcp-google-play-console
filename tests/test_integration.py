"""Integration tests against the real Google APIs.

Skipped by default (see addopts in pyproject.toml). Run with:

    GOOGLE_APPLICATION_CREDENTIALS=... pytest -m integration

They only read data and use one lightweight request per behavior under test.
"""

import os

import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"),
        reason="needs GOOGLE_APPLICATION_CREDENTIALS",
    ),
]

from play_console_mcp import server  # noqa: E402


def _tool(name):
    tool = getattr(server, name)
    return getattr(tool, "fn", tool)


@pytest.fixture(scope="module")
def package_names():
    apps = _tool("search_accessible_apps")(page_size=10).get("apps", [])
    if not apps:
        pytest.skip("credentials see no Play Console apps")
    return [app["packageName"] for app in apps]


def test_accessible_apps_have_expected_shape(package_names):
    assert all("." in name for name in package_names)


def test_single_day_query_returns_only_that_day(package_names):
    from datetime import date, timedelta

    for package_name in package_names:
        freshness = _tool("get_metric_set_freshness")(package_name, "crashRateMetricSet")
        daily = next(
            f["latestEndTime"]
            for f in freshness["freshnessInfo"]["freshnesses"]
            if f["aggregationPeriod"] == "DAILY"
        )
        # latestEndTime is an exclusive bound: the last full day is the day before.
        day = date(daily["year"], daily["month"], daily["day"]) - timedelta(days=1)
        result = _tool("query_metric_set")(
            package_name, "crashRateMetricSet", str(day), str(day)
        )
        rows = result.get("rows", [])
        if not rows:
            continue  # small apps fall below the vitals data threshold
        assert len(rows) == 1
        start = rows[0]["startTime"]
        assert (start["year"], start["month"], start["day"]) == (day.year, day.month, day.day)
        return
    pytest.skip("no accessible app has single-day vitals data")


def test_error_issue_search_accepts_single_day(package_names):
    from datetime import date, timedelta

    day = date.today() - timedelta(days=3)
    result = _tool("search_error_issues")(package_names[0], str(day), str(day), page_size=1)
    assert isinstance(result.get("errorIssues", []), list)
