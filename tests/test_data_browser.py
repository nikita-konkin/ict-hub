"""test_data_browser.py — Unit tests for data-indexer client XML parsing."""

from app import data_indexer_client as client


class _MockResponse:
    def __init__(self, text: str, status_code: int = 200):
        self.text = text
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def setup_function() -> None:
    client._cache.clear()


def test_rinex_xml_parsing_and_cache(monkeypatch) -> None:
    calls = {"count": 0}
    xml = (
        '<?xml version="1.0" encoding="UTF-8" ?>'
        '<rinex_structure>'
        '<item><year>2026_original</year><days>'
        '<item><day>01</day><stations>2</stations></item>'
        '<item><day>365</day><stations>1</stations></item>'
        '</days></item>'
        '</rinex_structure>'
    )

    def _mock_get(url: str, timeout: float, **kwargs):
        calls["count"] += 1
        return _MockResponse(xml)

    monkeypatch.setattr(client, "DATA_INDEXER_URL", "http://data-indexer:5001")
    monkeypatch.setattr(client.httpx, "get", _mock_get)

    first = client.list_rinex_server_structure("/mnt/rinex-server")
    second = client.list_rinex_server_structure("/mnt/rinex-server")

    assert first == [{"year": "2026_original", "days": [{"day": "01", "stations": 2}, {"day": "365", "stations": 1}]}]
    assert second == first
    assert calls["count"] == 1


def test_tecsuite_xml_parsing(monkeypatch) -> None:
    xml = (
        '<?xml version="1.0" encoding="UTF-8" ?>'
        '<tecsuite_structure>'
        '<item><year>2026</year><days>'
        '<item><day>003</day><sites><item>aksu</item><item>alex</item></sites></item>'
        '</days></item>'
        '</tecsuite_structure>'
    )

    monkeypatch.setattr(client, "DATA_INDEXER_URL", "http://data-indexer:5001")
    monkeypatch.setattr(client.httpx, "get", lambda url, timeout, **kw: _MockResponse(xml))

    result = client.list_tecsuite_output_structure("/mnt/tecsuite-out")

    assert result == [{"year": "2026", "days": [{"day": "003", "sites": ["aksu", "alex"]}]}]


def test_abstec_xml_parsing(monkeypatch) -> None:
    xml = (
        '<?xml version="1.0" encoding="UTF-8" ?>'
        '<abstec_structure>'
        '<item><year>2026</year><days>'
        '<item><day>010</day><sites><item>abcd</item></sites></item>'
        '</days></item>'
        '</abstec_structure>'
    )

    monkeypatch.setattr(client, "DATA_INDEXER_URL", "http://data-indexer:5001")
    monkeypatch.setattr(client.httpx, "get", lambda url, timeout, **kw: _MockResponse(xml))

    result = client.list_abstec_output_structure("/mnt/abstec-out")

    assert result == [{"year": "2026", "days": [{"day": "010", "sites": ["abcd"]}]}]


def test_parquet_xml_parsing(monkeypatch) -> None:
    xml = (
        '<?xml version="1.0" encoding="UTF-8" ?>'
        '<parquet_structure>'
        '<item><year>2026</year><days><item>001</item><item>007</item></days></item>'
        '</parquet_structure>'
    )

    monkeypatch.setattr(client, "DATA_INDEXER_URL", "http://data-indexer:5001")
    monkeypatch.setattr(client.httpx, "get", lambda url, timeout, **kw: _MockResponse(xml))

    result = client.list_parquet_output_structure("/mnt/tecsuite-parquet-out")

    assert result == [{"year": "2026", "days": ["001", "007"]}]
