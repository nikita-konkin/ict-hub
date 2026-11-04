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


def test_remove_abstec_parquet_previews_and_moves_only_selected(admin_client, monkeypatch, tmp_path):
    from app import indexed_data

    monkeypatch.setattr(indexed_data.cfg, "PARQUET_OUTPUT_ABSTEC_DATA_PATH_CONTAINER", str(tmp_path))
    selected = tmp_path / "2024" / "245" / "mork"
    selected.mkdir(parents=True)
    (selected / "result.parquet").write_text("old data")
    other = tmp_path / "2024" / "245" / "ntor"
    other.mkdir(parents=True)
    (other / "result.parquet").write_text("keep")
    params = {"year": "2024", "day_from": 245, "day_to": 245, "stations": "mork", "output_type": "parquet"}
    preview = admin_client.get("/indexed-data/remove-abstec", params=params)
    assert preview.status_code == 200
    assert "2024" in preview.text
    assert "mork" in preview.text
    root, targets = indexed_data._removal_targets("2024", 245, 245, "mork")
    digest = indexed_data._selection_digest([("parquet", root, targets)])
    (selected / "result.parquet").write_text("changed data")
    assert admin_client.post("/indexed-data/remove-abstec", data={**params, "digest": digest}).status_code == 409
    digest = indexed_data._selection_digest([("parquet", root, targets)])
    response = admin_client.post(
        "/indexed-data/remove-abstec", data={**params, "digest": digest}, follow_redirects=False
    )
    assert response.status_code == 303
    assert not selected.exists()
    assert (other / "result.parquet").read_text() == "keep"
    recovered = list((tmp_path / ".ionflow-trash").glob("*/2024/245/mork/result.parquet"))
    assert len(recovered) == 1
    assert recovered[0].read_text() == "changed data"


def test_remove_abstec_rejects_path_traversal(admin_client, monkeypatch, tmp_path):
    from app import indexed_data

    monkeypatch.setattr(indexed_data.cfg, "PARQUET_OUTPUT_ABSTEC_DATA_PATH_CONTAINER", str(tmp_path))
    response = admin_client.get(
        "/indexed-data/remove-abstec",
        params={"year": "2024", "day_from": 245, "day_to": 245, "stations": "../x", "output_type": "parquet"},
    )
    assert response.status_code == 400


def test_remove_abstec_requires_admin(operator_client):
    assert operator_client.get("/indexed-data/remove-abstec").status_code == 403
    assert (
        operator_client.post(
            "/indexed-data/remove-abstec",
            data={"year": "2024", "day_from": 1, "day_to": 1, "stations": "mork", "digest": "x"},
        ).status_code
        == 403
    )


def test_remove_both_abs_tec_outputs(admin_client, monkeypatch, tmp_path):
    from app import indexed_data

    dat_root = tmp_path / "dat"
    pq_root = tmp_path / "parquet"
    for root in (dat_root, pq_root):
        folder = root / "2024" / "245" / "mork"
        folder.mkdir(parents=True)
        (folder / "result.dat").write_text("result")
    monkeypatch.setattr(indexed_data.cfg, "ABSTEC_OUTPUT_DATA_PATH_CONTAINER", str(dat_root))
    monkeypatch.setattr(indexed_data.cfg, "PARQUET_OUTPUT_ABSTEC_DATA_PATH_CONTAINER", str(pq_root))
    params = {"year": "2024", "day_from": 245, "day_to": 245, "stations": "mork", "output_type": "both"}
    preview = admin_client.get("/indexed-data/remove-abstec", params=params)
    assert preview.status_code == 200
    assert "DAT:" in preview.text
    assert "PARQUET:" in preview.text
    selection = indexed_data._selection("2024", 245, 245, "mork", "both")
    response = admin_client.post(
        "/indexed-data/remove-abstec",
        data={**params, "digest": indexed_data._selection_digest(selection)},
        follow_redirects=False,
    )
    assert response.status_code == 303
    for root in (dat_root, pq_root):
        assert not (root / "2024" / "245" / "mork").exists()
        assert len(list((root / ".ionflow-trash").glob("*/2024/245/mork/result.dat"))) == 1
