"""
test_data_indexer_client.py — Unit tests for data_indexer_client helpers.

Coverage targets:
  - _parse_rinex_root       — XML → year/day/stations list
  - _parse_tecsuite_root    — XML → year/day/sites list
  - _parse_parquet_root     — XML → year/days list
  - _parse_parquet_sat_root — XML → year/day/stations/satellites list
  - _fetch_xml              — returns None when DATA_INDEXER_URL is empty
  - list_*_async            — returns [] when DATA_INDEXER_URL is empty
  - list_*_async            — cache hit avoids second HTTP call
  - list_*_async            — HTTP 4xx / network error → returns []
  - clear_cache             — flushes in-process cache
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _xml(text: str) -> ET.Element:
    """Parse an XML string into an Element for passing to _parse_* helpers."""
    return ET.fromstring(text)


# ─────────────────────────────────────────────────────────────────────────────
# _parse_rinex_root
# ─────────────────────────────────────────────────────────────────────────────

class TestParseRinexRoot:
    """Pure XML parsing — no HTTP, no filesystem."""

    from app.data_indexer_client import _parse_rinex_root

    def test_normal_year_and_days(self):
        from app.data_indexer_client import _parse_rinex_root
        root = _xml("""
        <root>
          <item>
            <year>2026_original</year>
            <days>
              <item><day>001</day><stations>5</stations></item>
              <item><day>010</day><stations>3</stations></item>
            </days>
          </item>
        </root>
        """)
        result = _parse_rinex_root(root)
        assert len(result) == 1
        assert result[0]["year"] == "2026_original"
        assert result[0]["days"] == [{"day": "001", "stations": 5}, {"day": "010", "stations": 3}]

    def test_multiple_years(self):
        from app.data_indexer_client import _parse_rinex_root
        root = _xml("""
        <root>
          <item><year>2025_original</year><days/></item>
          <item><year>2026_original</year><days/></item>
        </root>
        """)
        result = _parse_rinex_root(root)
        assert [r["year"] for r in result] == ["2025_original", "2026_original"]

    def test_item_without_year_is_skipped(self):
        from app.data_indexer_client import _parse_rinex_root
        root = _xml("<root><item><days/></item></root>")
        assert _parse_rinex_root(root) == []

    def test_item_without_days_node(self):
        from app.data_indexer_client import _parse_rinex_root
        root = _xml("<root><item><year>2026_original</year></item></root>")
        result = _parse_rinex_root(root)
        assert result[0]["days"] == []

    def test_day_item_without_day_text_is_skipped(self):
        from app.data_indexer_client import _parse_rinex_root
        root = _xml("""
        <root>
          <item>
            <year>2026_original</year>
            <days><item><stations>3</stations></item></days>
          </item>
        </root>
        """)
        result = _parse_rinex_root(root)
        assert result[0]["days"] == []

    def test_stations_defaults_to_zero_for_non_numeric(self):
        from app.data_indexer_client import _parse_rinex_root
        root = _xml("""
        <root>
          <item>
            <year>2026_original</year>
            <days><item><day>001</day><stations>bad</stations></item></days>
          </item>
        </root>
        """)
        result = _parse_rinex_root(root)
        assert result[0]["days"][0]["stations"] == 0

    def test_empty_root_returns_empty_list(self):
        from app.data_indexer_client import _parse_rinex_root
        assert _parse_rinex_root(_xml("<root/>")) == []


# ─────────────────────────────────────────────────────────────────────────────
# _parse_tecsuite_root
# ─────────────────────────────────────────────────────────────────────────────

class TestParseTecsuiteRoot:

    def test_normal_year_day_sites(self):
        from app.data_indexer_client import _parse_tecsuite_root
        root = _xml("""
        <root>
          <item>
            <year>2026</year>
            <days>
              <item>
                <day>001</day>
                <sites><item>aksu</item><item>alex</item></sites>
              </item>
            </days>
          </item>
        </root>
        """)
        result = _parse_tecsuite_root(root)
        assert result[0]["year"] == "2026"
        assert result[0]["days"][0]["day"] == "001"
        assert result[0]["days"][0]["sites"] == ["aksu", "alex"]

    def test_day_with_no_sites_node(self):
        from app.data_indexer_client import _parse_tecsuite_root
        root = _xml("""
        <root>
          <item>
            <year>2026</year>
            <days><item><day>001</day></item></days>
          </item>
        </root>
        """)
        result = _parse_tecsuite_root(root)
        assert result[0]["days"][0]["sites"] == []

    def test_day_with_empty_site_text_is_skipped(self):
        from app.data_indexer_client import _parse_tecsuite_root
        root = _xml("""
        <root>
          <item>
            <year>2026</year>
            <days>
              <item>
                <day>001</day>
                <sites><item/></sites>
              </item>
            </days>
          </item>
        </root>
        """)
        result = _parse_tecsuite_root(root)
        assert result[0]["days"][0]["sites"] == []

    def test_item_without_year_skipped(self):
        from app.data_indexer_client import _parse_tecsuite_root
        assert _parse_tecsuite_root(_xml("<root><item><days/></item></root>")) == []


# ─────────────────────────────────────────────────────────────────────────────
# _parse_parquet_root
# ─────────────────────────────────────────────────────────────────────────────

class TestParseParquetRoot:

    def test_normal_year_and_day_list(self):
        from app.data_indexer_client import _parse_parquet_root
        root = _xml("""
        <root>
          <item>
            <year>2026</year>
            <days><item>001</item><item>365</item></days>
          </item>
        </root>
        """)
        result = _parse_parquet_root(root)
        assert result[0]["year"] == "2026"
        assert result[0]["days"] == ["001", "365"]

    def test_empty_day_text_is_skipped(self):
        from app.data_indexer_client import _parse_parquet_root
        root = _xml("""
        <root>
          <item>
            <year>2026</year>
            <days><item>001</item><item/></days>
          </item>
        </root>
        """)
        result = _parse_parquet_root(root)
        assert result[0]["days"] == ["001"]

    def test_year_without_days_node(self):
        from app.data_indexer_client import _parse_parquet_root
        root = _xml("<root><item><year>2026</year></item></root>")
        assert _parse_parquet_root(root)[0]["days"] == []


# ─────────────────────────────────────────────────────────────────────────────
# _parse_parquet_sat_root
# ─────────────────────────────────────────────────────────────────────────────

class TestParseParquetSatRoot:

    def test_normal_full_structure(self):
        from app.data_indexer_client import _parse_parquet_sat_root
        root = _xml("""
        <root>
          <item>
            <year>2026</year>
            <days>
              <item>
                <day>001</day>
                <stations><item>aksu</item><item>arsk</item></stations>
                <satellites><item>E07</item><item>G12</item></satellites>
              </item>
            </days>
          </item>
        </root>
        """)
        result = _parse_parquet_sat_root(root)
        assert result[0]["year"] == "2026"
        day = result[0]["days"][0]
        assert day["day"] == "001"
        assert day["stations"] == ["aksu", "arsk"]
        assert day["satellites"] == ["E07", "G12"]

    def test_day_without_day_text_skipped(self):
        from app.data_indexer_client import _parse_parquet_sat_root
        root = _xml("""
        <root>
          <item>
            <year>2026</year>
            <days><item><stations/><satellites/></item></days>
          </item>
        </root>
        """)
        result = _parse_parquet_sat_root(root)
        assert result[0]["days"] == []

    def test_no_stations_or_satellites_nodes(self):
        from app.data_indexer_client import _parse_parquet_sat_root
        root = _xml("""
        <root>
          <item>
            <year>2026</year>
            <days><item><day>001</day></item></days>
          </item>
        </root>
        """)
        result = _parse_parquet_sat_root(root)
        day = result[0]["days"][0]
        assert day["stations"] == []
        assert day["satellites"] == []

    def test_multiple_years_and_days(self):
        from app.data_indexer_client import _parse_parquet_sat_root
        root = _xml("""
        <root>
          <item>
            <year>2025</year>
            <days>
              <item><day>365</day><stations><item>krsn</item></stations><satellites/></item>
            </days>
          </item>
          <item>
            <year>2026</year>
            <days>
              <item><day>001</day><stations/><satellites><item>E07</item></satellites></item>
              <item><day>002</day><stations/><satellites/></item>
            </days>
          </item>
        </root>
        """)
        result = _parse_parquet_sat_root(root)
        assert len(result) == 2
        assert result[1]["year"] == "2026"
        assert len(result[1]["days"]) == 2


# ─────────────────────────────────────────────────────────────────────────────
# _fetch_xml — no-URL guard
# ─────────────────────────────────────────────────────────────────────────────

class TestFetchXml:

    def test_returns_none_when_url_not_configured(self, monkeypatch):
        import app.data_indexer_client as client_module
        monkeypatch.setattr(client_module, "DATA_INDEXER_URL", "")
        from app.data_indexer_client import _fetch_xml
        result = _fetch_xml("rinex", "/some/path")
        assert result is None

    def test_returns_none_on_http_error(self, monkeypatch):
        import app.data_indexer_client as client_module
        monkeypatch.setattr(client_module, "DATA_INDEXER_URL", "http://localhost:5001")

        mock_response = MagicMock()
        mock_response.status_code = 503
        mock_response.headers = {"server": "nginx"}
        mock_response.text = "Service Unavailable"
        mock_response.raise_for_status.side_effect = Exception("503")

        with patch("httpx.get", return_value=mock_response):
            from app.data_indexer_client import _fetch_xml
            result = _fetch_xml("rinex", "/some/path")
        assert result is None

    def test_returns_none_on_network_error(self, monkeypatch):
        import app.data_indexer_client as client_module
        monkeypatch.setattr(client_module, "DATA_INDEXER_URL", "http://localhost:5001")

        with patch("httpx.get", side_effect=Exception("Connection refused")):
            from app.data_indexer_client import _fetch_xml
            result = _fetch_xml("rinex", "/some/path")
        assert result is None


# ─────────────────────────────────────────────────────────────────────────────
# Async functions — common behavior
# ─────────────────────────────────────────────────────────────────────────────

class TestAsyncFunctions:
    """Tests for list_*_async — cache hits, empty URL guard, error handling."""

    @pytest.fixture(autouse=True)
    def clear_cache(self):
        """Ensure the module-level cache is empty before every test."""
        from app.data_indexer_client import clear_cache
        clear_cache()
        yield
        clear_cache()

    # ----- empty DATA_INDEXER_URL guard -----

    async def test_parquet_sat_returns_empty_when_url_not_configured(self, monkeypatch):
        import app.data_indexer_client as m
        monkeypatch.setattr(m, "DATA_INDEXER_URL", "")
        from app.data_indexer_client import list_parquet_satellite_structure_async
        assert await list_parquet_satellite_structure_async("/any") == []

    async def test_rinex_returns_empty_when_url_not_configured(self, monkeypatch):
        import app.data_indexer_client as m
        monkeypatch.setattr(m, "DATA_INDEXER_URL", "")
        from app.data_indexer_client import list_rinex_server_structure_async
        assert await list_rinex_server_structure_async("/any") == []

    async def test_tecsuite_returns_empty_when_url_not_configured(self, monkeypatch):
        import app.data_indexer_client as m
        monkeypatch.setattr(m, "DATA_INDEXER_URL", "")
        from app.data_indexer_client import list_tecsuite_output_structure_async
        assert await list_tecsuite_output_structure_async("/any") == []

    async def test_parquet_output_returns_empty_when_url_not_configured(self, monkeypatch):
        import app.data_indexer_client as m
        monkeypatch.setattr(m, "DATA_INDEXER_URL", "")
        from app.data_indexer_client import list_parquet_output_structure_async
        assert await list_parquet_output_structure_async("/any") == []

    async def test_rinex_station_map_returns_empty_payload_when_url_not_configured(self, monkeypatch):
        import app.data_indexer_client as m
        monkeypatch.setattr(m, "DATA_INDEXER_URL", "")
        from app.data_indexer_client import get_rinex_station_map_async

        result = await get_rinex_station_map_async("/any", year="2026_original", day="001")
        assert result["year"] == "2026_original"
        assert result["day"] == "001"
        assert result["station_count"] == 0
        assert result["stations"] == []

    # ----- HTTP error → empty list -----

    async def test_parquet_sat_returns_empty_on_upstream_error(self, monkeypatch):
        import app.data_indexer_client as m
        monkeypatch.setattr(m, "DATA_INDEXER_URL", "http://data-indexer:5001")

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get.side_effect = Exception("connection refused")

        with patch("httpx.AsyncClient", return_value=mock_client):
            from app.data_indexer_client import list_parquet_satellite_structure_async
            result = await list_parquet_satellite_structure_async("/mnt/parquet")
        assert result == []

    async def test_rinex_returns_empty_on_upstream_4xx(self, monkeypatch):
        import app.data_indexer_client as m
        monkeypatch.setattr(m, "DATA_INDEXER_URL", "http://data-indexer:5001")

        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_resp.headers = {}
        mock_resp.text = "not found"
        mock_resp.raise_for_status.side_effect = Exception("404")

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get.return_value = mock_resp

        with patch("httpx.AsyncClient", return_value=mock_client):
            from app.data_indexer_client import list_rinex_server_structure_async
            result = await list_rinex_server_structure_async("/mnt/rinex")
        assert result == []

    # ----- Cache hit skips HTTP calls -----

    async def test_second_call_uses_cache_no_http(self, monkeypatch):
        """After a successful fetch, a second call with the same path must not hit HTTP."""
        import app.data_indexer_client as m
        monkeypatch.setattr(m, "DATA_INDEXER_URL", "http://data-indexer:5001")

        xml_body = """<root>
          <item>
            <year>2026</year>
            <days>
              <item>
                <day>001</day>
                <stations><item>aksu</item></stations>
                <satellites><item>E07</item></satellites>
              </item>
            </days>
          </item>
        </root>"""

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {}
        mock_resp.text = xml_body
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get.return_value = mock_resp

        with patch("httpx.AsyncClient", return_value=mock_client):
            from app.data_indexer_client import list_parquet_satellite_structure_async
            first = await list_parquet_satellite_structure_async("/mnt/parquet")
            second = await list_parquet_satellite_structure_async("/mnt/parquet")

        assert first == second
        assert first[0]["year"] == "2026"
        # HTTP was called exactly once (cache served the second call)
        assert mock_client.get.call_count == 1

    async def test_rinex_station_map_second_call_uses_cache(self, monkeypatch):
        import app.data_indexer_client as m
        monkeypatch.setattr(m, "DATA_INDEXER_URL", "http://data-indexer:5001")

        fetch_calls: list[tuple[str, str, str]] = []

        async def _fake_fetch_json(endpoint: str, root_path: str, *, refresh: bool = False, extra_query=None):
            fetch_calls.append((endpoint, root_path, str((extra_query or {}).get("day", ""))))
            return {
                "year": "2026_original",
                "day": "001",
                "station_count": 1,
                "archive_count": 2,
                "stations": [{"station_id": "AKSU"}],
            }

        with patch.object(m, "_fetch_json_async", side_effect=_fake_fetch_json):
            from app.data_indexer_client import get_rinex_station_map_async

            first = await get_rinex_station_map_async("/mnt/rinex", year="2026_original", day="001")
            second = await get_rinex_station_map_async("/mnt/rinex", year="2026_original", day="001")

        assert first == second
        assert first["station_count"] == 1
        assert fetch_calls == [("rinex-stations", "/mnt/rinex", "001")]

    async def test_different_paths_are_cached_independently(self, monkeypatch):
        import app.data_indexer_client as m
        monkeypatch.setattr(m, "DATA_INDEXER_URL", "http://data-indexer:5001")

        def _make_xml(year: str) -> str:
            return f"<root><item><year>{year}</year><days/></item></root>"

        call_count = 0

        async def _fake_fetch(endpoint: str, root_path: str, refresh: bool = False):
            nonlocal call_count
            call_count += 1
            year = "2026" if root_path == "/mnt/tecsuite" else "2025"
            return ET.fromstring(_make_xml(year))

        with patch.object(m, "_fetch_xml_async", side_effect=_fake_fetch):
            from app.data_indexer_client import list_parquet_satellite_structure_async
            r1 = await list_parquet_satellite_structure_async("/mnt/tecsuite")
            r2 = await list_parquet_satellite_structure_async("/mnt/abstec")
            _ = await list_parquet_satellite_structure_async("/mnt/tecsuite")  # should hit cache

        assert r1[0]["year"] == "2026"
        assert r2[0]["year"] == "2025"
        assert call_count == 2  # third call used cache

    # ----- clear_cache flushes everything -----

    async def test_clear_cache_forces_refetch(self, monkeypatch):
        import app.data_indexer_client as m
        monkeypatch.setattr(m, "DATA_INDEXER_URL", "http://data-indexer:5001")

        fetch_calls: list[str] = []

        async def _fake_fetch(endpoint: str, root_path: str, refresh: bool = False):
            fetch_calls.append(root_path)
            return ET.fromstring("<root><item><year>2026</year><days/></item></root>")

        with patch.object(m, "_fetch_xml_async", side_effect=_fake_fetch):
            from app.data_indexer_client import clear_cache, list_rinex_server_structure_async
            await list_rinex_server_structure_async("/mnt/rinex")
            clear_cache()
            await list_rinex_server_structure_async("/mnt/rinex")

        assert len(fetch_calls) == 2  # fetched twice — cache was cleared between calls
