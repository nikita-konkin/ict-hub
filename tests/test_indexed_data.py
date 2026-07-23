"""Tests for the indexed data browser page."""

from fastapi.testclient import TestClient

from app.indexed_data import _day_labels, _with_doy_days


class TestDayLabels:
    """Every section must present days as DOY, whatever shape the indexer returns."""

    def test_doy_folder_is_zero_padded(self):
        assert _day_labels("15", "2024_original") == ("015", "2024-01-15")
        assert _day_labels("015", "2024") == ("015", "2024-01-15")

    def test_month_day_folder_is_converted_to_doy(self):
        # RINEX layout B stores MM/DD, which used to render as a date.
        assert _day_labels("01/15", "2024_original") == ("015", "2024-01-15")
        assert _day_labels("12/31", "2024_original") == ("366", "2024-12-31")
        assert _day_labels("12/31", "2023_original") == ("365", "2023-12-31")

    def test_leap_day_boundary(self):
        assert _day_labels("366", "2024") == ("366", "2024-12-31")
        # 2023 has no DOY 366 — keep the label but offer no calendar date.
        assert _day_labels("366", "2023") == ("366", "")

    def test_unknown_year_still_yields_doy(self):
        assert _day_labels("15", "") == ("015", "")

    def test_unparsable_folder_names_pass_through(self):
        assert _day_labels("02/30", "2024_original") == ("02/30", "")
        assert _day_labels("weird", "2024") == ("weird", "")


class TestWithDoyDays:
    def test_dict_days_keep_their_payload(self):
        tree = [{"year": "2024_original", "days": [{"day": "01/15", "stations": 7}]}]
        assert _with_doy_days(tree) == [
            {
                "year": "2024_original",
                "days": [{"day": "015", "stations": 7, "date": "2024-01-15"}],
            }
        ]

    def test_string_days_are_normalized_in_place(self):
        tree = [{"year": "2024", "days": ["1", "015", "7"]}]
        assert _with_doy_days(tree) == [{"year": "2024", "days": ["001", "015", "007"]}]

    def test_empty_trees_are_safe(self):
        assert _with_doy_days([]) == []
        assert _with_doy_days([{"year": "2024", "days": []}]) == [{"year": "2024", "days": []}]


def test_indexed_data_requires_auth(client: TestClient):
    response = client.get("/indexed-data", follow_redirects=False)
    assert response.status_code == 303


def test_indexed_data_page_renders_days_as_doy(admin_client: TestClient, monkeypatch):
    import app.indexed_data as indexed_data_module

    async def _rinex_tree(root: str, refresh: bool = False):
        return [{"year": "2024_original", "days": [{"day": "01/15", "stations": 5}]}]

    async def _tecsuite_tree(root: str, refresh: bool = False):
        return [{"year": "2024", "days": [{"day": "015", "sites": ["novm", "irkj"]}]}]

    async def _parquet_tree(root: str, refresh: bool = False):
        return [{"year": "2024", "days": ["15"]}]

    monkeypatch.setattr(indexed_data_module.cfg, "DATA_INDEXER_URL", "http://data-indexer:5001")
    monkeypatch.setattr(indexed_data_module.cfg, "RINEX_DATA_PATH_CONTAINER", "/mnt/rinex-server")
    monkeypatch.setattr(indexed_data_module.cfg, "TECSUITE_OUT_DAT_DATA_PATH_CONTAINER", "/mnt/tecsuite")
    monkeypatch.setattr(indexed_data_module.cfg, "ABSTEC_OUTPUT_DATA_PATH_CONTAINER", "/mnt/abstec")
    monkeypatch.setattr(indexed_data_module.cfg, "PARQUET_OUTPUT_TECSUITE_DATA_PATH_CONTAINER", "/mnt/pq-tecsuite")
    monkeypatch.setattr(indexed_data_module.cfg, "PARQUET_OUTPUT_ABSTEC_DATA_PATH_CONTAINER", "/mnt/pq-abstec")
    monkeypatch.setattr(indexed_data_module, "list_rinex_server_structure_async", _rinex_tree)
    monkeypatch.setattr(indexed_data_module, "list_tecsuite_output_structure_async", _tecsuite_tree)
    monkeypatch.setattr(indexed_data_module, "list_abstec_output_structure_async", _tecsuite_tree)
    monkeypatch.setattr(indexed_data_module, "list_parquet_output_structure_async", _parquet_tree)

    response = admin_client.get("/indexed-data")
    assert response.status_code == 200

    html = response.text
    # The RINEX MM/DD folder must no longer be rendered as a date.
    assert "01/15" not in html
    assert "015" in html
    # The calendar date stays reachable as a tooltip.
    assert 'title="DOY 015 · 2024-01-15"' in html
