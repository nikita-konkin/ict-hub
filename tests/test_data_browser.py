"""test_data_browser.py — data-indexer client: HTTP → XML parsing → cache."""

import pytest

from app import data_indexer_client as client


class _FakeResponse:
    def __init__(self, text: str, status_code: int = 200):
        self.text = text
        self.status_code = status_code
        self.headers: dict[str, str] = {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


@pytest.fixture()
def indexer_serving(monkeypatch):
    """Make every indexer request return the given XML; returns the list of requested URLs."""
    requested: list[str] = []

    def _serve(xml: str):
        class _FakeAsyncClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def get(self, url):
                requested.append(url)
                return _FakeResponse(xml)

        monkeypatch.setattr(client, "DATA_INDEXER_URL", "http://data-indexer:5001")
        monkeypatch.setattr(client.httpx, "AsyncClient", _FakeAsyncClient)
        return requested

    client.clear_cache()
    yield _serve
    client.clear_cache()


async def test_rinex_xml_parsing_and_cache(indexer_serving) -> None:
    requested = indexer_serving(
        '<?xml version="1.0" encoding="UTF-8" ?>'
        '<rinex_structure>'
        '<item><year>2026_original</year><days>'
        '<item><day>01</day><stations>2</stations></item>'
        '<item><day>365</day><stations>1</stations></item>'
        '</days></item>'
        '</rinex_structure>'
    )

    first = await client.list_rinex_server_structure_async("/mnt/rinex-server")
    second = await client.list_rinex_server_structure_async("/mnt/rinex-server")

    assert first == [{"year": "2026_original", "days": [{"day": "01", "stations": 2}, {"day": "365", "stations": 1}]}]
    assert second == first
    assert requested == ["http://data-indexer:5001/rinex?root=/mnt/rinex-server"]


async def test_tecsuite_xml_parsing(indexer_serving) -> None:
    indexer_serving(
        '<?xml version="1.0" encoding="UTF-8" ?>'
        '<tecsuite_structure>'
        '<item><year>2026</year><days>'
        '<item><day>003</day><sites><item>aksu</item><item>alex</item></sites></item>'
        '</days></item>'
        '</tecsuite_structure>'
    )

    result = await client.list_tecsuite_output_structure_async("/mnt/tecsuite-out")

    assert result == [{"year": "2026", "days": [{"day": "003", "sites": ["aksu", "alex"]}]}]


async def test_parquet_xml_parsing(indexer_serving) -> None:
    indexer_serving(
        '<?xml version="1.0" encoding="UTF-8" ?>'
        '<parquet_structure>'
        '<item><year>2026</year><days><item>001</item><item>007</item></days></item>'
        '</parquet_structure>'
    )

    result = await client.list_parquet_output_structure_async("/mnt/tecsuite-parquet-out")

    assert result == [{"year": "2026", "days": ["001", "007"]}]


async def test_cache_entries_expire(indexer_serving, monkeypatch) -> None:
    requested = indexer_serving("<parquet_structure/>")
    monkeypatch.setattr(client, "DATA_INDEXER_CLIENT_CACHE_TTL_SEC", 0.0)

    await client.list_parquet_output_structure_async("/mnt/p")
    await client.list_parquet_output_structure_async("/mnt/p")

    assert len(requested) == 2
