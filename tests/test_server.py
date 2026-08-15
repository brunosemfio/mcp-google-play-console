import gzip

import pytest

from play_console_mcp import api, server


def _tool(name):
    tool = getattr(server, name)
    return getattr(tool, "fn", tool)


def test_query_metric_set_builds_daily_body(monkeypatch):
    calls = {}

    def fake_post(path, body):
        calls["path"], calls["body"] = path, body
        return {"rows": []}

    monkeypatch.setattr(api, "reporting_post", fake_post)
    _tool("query_metric_set")(
        "com.example.app", "crashRateMetricSet", "2026-08-01", "2026-08-14"
    )
    assert calls["path"] == "apps/com.example.app/crashRateMetricSet:query"
    spec = calls["body"]["timelineSpec"]
    assert spec["aggregationPeriod"] == "DAILY"
    assert spec["startTime"]["timeZone"]["id"] == "America/Los_Angeles"
    assert calls["body"]["metrics"] == server.METRIC_SETS["crashRateMetricSet"]


def test_query_metric_set_hourly_uses_utc(monkeypatch):
    calls = {}
    monkeypatch.setattr(api, "reporting_post", lambda path, body: calls.update(body=body) or {})
    _tool("query_metric_set")(
        "com.example.app",
        "crashRateMetricSet",
        "2026-08-01",
        "2026-08-02",
        aggregation_period="HOURLY",
    )
    assert calls["body"]["timelineSpec"]["startTime"]["timeZone"]["id"] == "UTC"


def test_rejects_unknown_metric_set_and_invalid_package():
    with pytest.raises(ValueError, match="Unknown metric set"):
        _tool("get_metric_set_freshness")("com.example.app", "salesMetricSet")
    with pytest.raises(ValueError, match="package_name"):
        _tool("search_error_issues")("../secret", "2026-08-01", "2026-08-02")


def test_date_time_rejects_bad_date():
    with pytest.raises(api.PlayConsoleApiError, match="YYYY-MM-DD"):
        api.date_time("01/08/2026", "UTC")


def test_single_day_query_gets_exclusive_end_time(monkeypatch):
    calls = {}
    monkeypatch.setattr(api, "reporting_post", lambda path, body: calls.update(body=body) or {})
    _tool("query_metric_set")(
        "com.example.app", "crashRateMetricSet", "2026-08-13", "2026-08-13"
    )
    end = calls["body"]["timelineSpec"]["endTime"]
    assert (end["year"], end["month"], end["day"]) == (2026, 8, 14)


def test_next_day_crosses_month_boundary():
    assert api.next_day("2026-08-31") == "2026-09-01"


def test_rejects_invalid_aggregation_period_and_inverted_range():
    with pytest.raises(ValueError, match="aggregation_period"):
        _tool("query_metric_set")(
            "com.example.app", "crashRateMetricSet", "2026-08-01", "2026-08-02",
            aggregation_period="WEEKLY",
        )
    with pytest.raises(ValueError, match="after end_date"):
        _tool("search_error_issues")("com.example.app", "2026-08-10", "2026-08-01")


def test_download_rejects_oversized_payload_despite_metadata(monkeypatch):
    # Metadata may lie (or the object changed between calls): the byte check
    # after download must still hold.
    monkeypatch.setattr(api, "storage_metadata", lambda bucket, name: {"size": 10})
    monkeypatch.setattr(api, "storage_download", lambda bucket, name: b"x" * 2048)
    with pytest.raises(ValueError, match="exceeds max_bytes"):
        _tool("download_stats_report")("stats/report.csv", bucket="b", max_bytes=1024)


def _fake_storage(monkeypatch, payload: bytes):
    monkeypatch.setattr(api, "storage_metadata", lambda bucket, name: {"size": len(payload)})
    monkeypatch.setattr(api, "storage_download", lambda bucket, name: payload)


def test_download_decodes_utf16_bom(monkeypatch):
    _fake_storage(monkeypatch, "Origem,Instalações\nPesquisa,42\n".encode("utf-16"))
    result = _tool("download_stats_report")("stats/installs/report.csv", bucket="b")
    assert result["rows"] == [{"Origem": "Pesquisa", "Instalações": "42"}]
    assert result["truncated"] is False


def test_download_decodes_utf8_without_bom(monkeypatch):
    # Even-length UTF-8 payload: must not be misread as UTF-16.
    _fake_storage(monkeypatch, b"date,value\n2026-01-01,7\n2026-01-02,8\n")
    result = _tool("download_stats_report")("reviews/report.csv", bucket="b", max_rows=1)
    assert result["rows"] == [{"date": "2026-01-01", "value": "7"}]
    assert result["truncated"] is True


def test_download_handles_gzip(monkeypatch):
    _fake_storage(monkeypatch, gzip.compress(b"a,b\n1,2\n"))
    result = _tool("download_stats_report")("earnings/report.csv.gz", bucket="b")
    assert result["rows"] == [{"a": "1", "b": "2"}]


def test_download_rejects_unsafe_object_name():
    with pytest.raises(ValueError, match="relative object path"):
        _tool("download_stats_report")("../credentials.json", bucket="b")


def test_download_checks_size_before_fetching(monkeypatch):
    monkeypatch.setattr(api, "storage_metadata", lambda bucket, name: {"size": 99_999_999})

    def fail_download(bucket, name):
        raise AssertionError("must not download oversized files")

    monkeypatch.setattr(api, "storage_download", fail_download)
    with pytest.raises(ValueError, match="max_bytes"):
        _tool("download_stats_report")("stats/huge.csv", bucket="b")


def test_bucket_env_and_gs_prefix(monkeypatch):
    monkeypatch.delenv(server.BUCKET_ENV, raising=False)
    with pytest.raises(ValueError, match=server.BUCKET_ENV):
        server._bucket(None)
    assert server._bucket("gs://pubsite_prod_rev_123/") == "pubsite_prod_rev_123"
    assert server._bucket("gs://pubsite_prod_rev_123/stats/installs/") == "pubsite_prod_rev_123"
