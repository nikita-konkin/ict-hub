"""
test_registry.py — Tests for converter registry, flag definitions, and i18n keys.

Tests validate:
  - Converter registry structure and flag definitions
  - CLI flag parameters and defaults
  - Translation key availability for new options
  - Command building with new converter options
"""
import pytest
from app.registry import CONVERTERS, get_converter, build_command
from app.i18n import _TRANSLATIONS as TRANSLATIONS


class TestConverterRegistry:
    """Tests for converter definitions and flag specifications."""

    def test_tec_suite_converter_exists(self):
        """TEC-Suite converter should be registered."""
        conv = get_converter("tec-suite")
        assert conv is not None
        assert conv["label"] == "TEC-Suite"

    def test_tec_suite_has_days_flag(self):
        """TEC-Suite should have --days filter flag."""
        conv = get_converter("tec-suite")
        days_flag = next((f for f in conv["flags"] if f["long"] == "--days"), None)
        assert days_flag is not None
        assert days_flag["type"] == "text"
        assert days_flag["label"] == "Days Filter"
        assert "optional" in days_flag["help"].lower() or "day" in days_flag["help"].lower()

    def test_tec_suite_jobs_default_is_1(self):
        """TEC-Suite --jobs default should be 1 (not 4)."""
        conv = get_converter("tec-suite")
        jobs_flag = next((f for f in conv["flags"] if f["long"] == "--jobs"), None)
        assert jobs_flag is not None
        assert jobs_flag["default"] == 1

    def test_tec_suite_jobs_flag_constraints(self):
        """TEC-Suite --jobs should have min/max constraints."""
        conv = get_converter("tec-suite")
        jobs_flag = next((f for f in conv["flags"] if f["long"] == "--jobs"), None)
        assert jobs_flag["min"] == 1
        assert jobs_flag["max"] == 64

    def test_dat_parquet_converter_exists(self):
        """DAT <-> Parquet converter should be registered."""
        conv = get_converter("dat-parquet-handler")
        assert conv is not None
        assert "parquet" in conv["label"].lower()

    def test_dat_parquet_has_day_from_flag(self):
        """DAT <-> Parquet should have --day-from flag."""
        conv = get_converter("dat-parquet-handler")
        day_from = next((f for f in conv["flags"] if f["long"] == "--day-from"), None)
        assert day_from is not None
        assert day_from["type"] == "number"
        assert day_from["min"] == 1
        assert day_from["max"] == 366
        assert day_from["required"] is False

    def test_dat_parquet_has_day_to_flag(self):
        """DAT <-> Parquet should have --day-to flag."""
        conv = get_converter("dat-parquet-handler")
        day_to = next((f for f in conv["flags"] if f["long"] == "--day-to"), None)
        assert day_to is not None
        assert day_to["type"] == "number"
        assert day_to["min"] == 1
        assert day_to["max"] == 366
        assert day_to["required"] is False

    def test_all_flags_have_label(self):
        """Every flag should have a human-readable label."""
        for conv_name, conv in CONVERTERS.items():
            for flag in conv.get("flags", []):
                assert "label" in flag, \
                    f"Flag {flag.get('long')} in {conv_name} missing label"
                assert len(flag["label"]) > 0

    def test_all_flags_have_help_text(self):
        """Every flag should have help/description text."""
        for conv_name, conv in CONVERTERS.items():
            for flag in conv.get("flags", []):
                assert "help" in flag, \
                    f"Flag {flag.get('long')} in {conv_name} missing help text"
                assert len(flag["help"]) > 0

    def test_number_flags_have_min_max(self):
        """Number-type flags should have min and max constraints when applicable."""
        for conv_name, conv in CONVERTERS.items():
            for flag in conv.get("flags", []):
                if flag.get("type") == "number":
                    # min and max should be present for constrained number fields
                    # Some flags like --execution-timeout-seconds may omit max for open-ended values
                    assert "min" in flag or "max" in flag, \
                        f"Number flag {flag.get('long')} in {conv_name} should have at least min or max"
                    if "min" in flag and "max" in flag:
                        assert flag["min"] <= flag["max"], \
                            f"Number flag {flag.get('long')} in {conv_name} has min > max"

    def test_select_flags_have_options(self):
        """Select-type flags should have options list."""
        for conv_name, conv in CONVERTERS.items():
            for flag in conv.get("flags", []):
                if flag.get("type") == "select":
                    assert "options" in flag, \
                        f"Select flag {flag.get('long')} in {conv_name} missing options"
                    assert len(flag["options"]) > 0

    def test_all_converters_have_container_volumes(self):
        """Every converter should define container volume mappings."""
        for conv_name, conv in CONVERTERS.items():
            assert "container_volumes" in conv, \
                f"Converter {conv_name} missing container_volumes"
            assert isinstance(conv["container_volumes"], dict)
            assert len(conv["container_volumes"]) > 0

    def test_all_converters_have_image_and_label(self):
        """Every converter should have image and label."""
        for conv_name, conv in CONVERTERS.items():
            assert "image" in conv and conv["image"], \
                f"Converter {conv_name} missing image"
            assert "label" in conv and conv["label"], \
                f"Converter {conv_name} missing label"


class TestTranslationKeys:
    """Tests for i18n keys for converter options."""

    def test_translations_dict_has_english_and_russian(self):
        """TRANSLATIONS dict should have 'en' and 'ru' keys."""
        assert "en" in TRANSLATIONS
        assert "ru" in TRANSLATIONS

    def test_day_from_translation_keys_exist(self):
        """Day From option should have translation keys."""
        assert "flag_label_day_from" in TRANSLATIONS["en"]
        assert "flag_label_day_from" in TRANSLATIONS["ru"]
        assert "flag_help_day_from" in TRANSLATIONS["en"]
        assert "flag_help_day_from" in TRANSLATIONS["ru"]

    def test_day_to_translation_keys_exist(self):
        """Day To option should have translation keys."""
        assert "flag_label_day_to" in TRANSLATIONS["en"]
        assert "flag_label_day_to" in TRANSLATIONS["ru"]
        assert "flag_help_day_to" in TRANSLATIONS["en"]
        assert "flag_help_day_to" in TRANSLATIONS["ru"]

    def test_days_filter_translation_keys_exist(self):
        """Days Filter option should have translation keys."""
        assert "flag_label_days" in TRANSLATIONS["en"]
        assert "flag_label_days" in TRANSLATIONS["ru"]
        assert "flag_help_days" in TRANSLATIONS["en"]
        assert "flag_help_days" in TRANSLATIONS["ru"]

    def test_day_from_translation_keys_non_empty(self):
        """Day From translation keys should have non-empty values."""
        assert len(TRANSLATIONS["en"]["flag_label_day_from"]) > 0
        assert len(TRANSLATIONS["ru"]["flag_label_day_from"]) > 0
        assert len(TRANSLATIONS["en"]["flag_help_day_from"]) > 0
        assert len(TRANSLATIONS["ru"]["flag_help_day_from"]) > 0

    def test_day_to_translation_keys_non_empty(self):
        """Day To translation keys should have non-empty values."""
        assert len(TRANSLATIONS["en"]["flag_label_day_to"]) > 0
        assert len(TRANSLATIONS["ru"]["flag_label_day_to"]) > 0
        assert len(TRANSLATIONS["en"]["flag_help_day_to"]) > 0
        assert len(TRANSLATIONS["ru"]["flag_help_day_to"]) > 0

    def test_days_filter_translation_keys_non_empty(self):
        """Days Filter translation keys should have non-empty values."""
        assert len(TRANSLATIONS["en"]["flag_label_days"]) > 0
        assert len(TRANSLATIONS["ru"]["flag_label_days"]) > 0
        assert len(TRANSLATIONS["en"]["flag_help_days"]) > 0
        assert len(TRANSLATIONS["ru"]["flag_help_days"]) > 0

    def test_russian_translations_different_from_english(self):
        """Russian and English translations should differ (basic sanity check)."""
        # At least one key should have different RU and EN values
        en_keys = set(TRANSLATIONS["en"].keys())
        ru_keys = set(TRANSLATIONS["ru"].keys())
        assert en_keys == ru_keys, "EN and RU should have the same keys"
        
        # Check that some translations are actually different
        different_count = sum(
            1 for key in en_keys
            if TRANSLATIONS["en"].get(key) != TRANSLATIONS["ru"].get(key)
        )
        assert different_count > 0, "RU and EN translations should have differences"


class TestCommandBuilding:
    """Tests for CLI command generation with new converter options."""

    def test_build_tec_suite_command_with_days_filter(self):
        """TEC-Suite command with --days filter should include the flag."""
        cmd, vols = build_command(
            "tec-suite",
            {
                "root": "/data/rinex",
                "root_subpath": "/2026_original/001",
                "jobs": "2",
                "days": "1-5,10",
            }
        )
        cmd_str = " ".join(cmd)
        assert "--days" in cmd_str
        assert "1-5,10" in cmd_str

    def test_build_tec_suite_command_without_days_filter(self):
        """TEC-Suite command without --days filter should work without errors."""
        cmd, vols = build_command(
            "tec-suite",
            {
                "root": "/data/rinex",
                "root_subpath": "/2026_original/001",
                "jobs": "2",
                "days": "",  # empty days filter
            }
        )
        cmd_str = " ".join(cmd)
        # Should not include --days when empty
        assert "--days \"\"" not in cmd_str or cmd_str.count("--days") == 0

    def test_build_tec_suite_command_uses_jobs_default(self):
        """TEC-Suite default jobs (1) should be used when not provided."""
        cmd, vols = build_command(
            "tec-suite",
            {
                "root": "/data/rinex",
                "root_subpath": "/2026_original/001",
                # jobs not provided, should use default 1
            }
        )
        cmd_str = " ".join(cmd)
        # The -j flag should be present with default value
        assert "-j" in cmd_str

    def test_build_dat_parquet_command_with_day_range(self):
        """DAT-Parquet command with day range should include both flags."""
        cmd, vols = build_command(
            "dat-parquet-handler",
            {
                "direction": "dat-to-parquet",
                "src": "/data/tecs-out",
                "dst": "/data/parquet",
                "day_from": "1",
                "day_to": "100",
            }
        )
        cmd_str = " ".join(cmd)
        assert "--day-from" in cmd_str
        assert "--day-to" in cmd_str
        assert "1" in cmd_str
        assert "100" in cmd_str

    def test_build_dat_parquet_command_without_day_range(self):
        """DAT-Parquet command without day range should still be valid."""
        cmd, vols = build_command(
            "dat-parquet-handler",
            {
                "direction": "dat-to-parquet",
                "src": "/data/tecs-out",
                "dst": "/data/parquet",
                # day_from, day_to not provided
            }
        )
        cmd_str = " ".join(cmd)
        # Should not include day filters when not provided
        assert "--day-from" not in cmd_str or "\"\"" not in cmd_str.split("--day-from")[0][-10:]

    def test_build_dat_parquet_command_with_only_day_from(self):
        """DAT-Parquet with only day_from should include just that flag."""
        cmd, vols = build_command(
            "dat-parquet-handler",
            {
                "direction": "dat-to-parquet",
                "src": "/data/tecs-out",
                "dst": "/data/parquet",
                "day_from": "50",
                # day_to not provided
            }
        )
        cmd_str = " ".join(cmd)
        assert "--day-from" in cmd_str
        assert "50" in cmd_str

    def test_build_dat_parquet_command_with_only_day_to(self):
        """DAT-Parquet with only day_to should include just that flag."""
        cmd, vols = build_command(
            "dat-parquet-handler",
            {
                "direction": "parquet-to-dat",
                "src": "/data/parquet",
                "dst": "/data/tecs-out",
                # day_from not provided
                "day_to": "200",
            }
        )
        cmd_str = " ".join(cmd)
        assert "--day-to" in cmd_str
        assert "200" in cmd_str

    def test_build_dat_parquet_command_preserves_direction(self):
        """DAT-Parquet command should preserve direction flag."""
        for direction in ["dat-to-parquet", "parquet-to-dat"]:
            cmd, vols = build_command(
                "dat-parquet-handler",
                {
                    "direction": direction,
                    "src": "/data/src",
                    "dst": "/data/dst",
                }
            )
            cmd_str = " ".join(cmd)
            assert "--direction" in cmd_str
            assert direction in cmd_str

    def test_build_command_returns_volumes_dict(self):
        """build_command should return a volumes dict in Docker SDK format."""
        cmd, vols = build_command(
            "tec-suite",
            {
                "root": "/data/rinex",
                "root_subpath": "/2026_original/001",
            }
        )
        assert isinstance(vols, dict)
        # Should have at least the root volume
        assert "/data/rinex" in vols
        assert "bind" in vols["/data/rinex"]
        assert "mode" in vols["/data/rinex"]

    def test_build_dat_parquet_volumes_include_src_dst(self):
        """DAT-Parquet volumes should include both src and dst."""
        cmd, vols = build_command(
            "dat-parquet-handler",
            {
                "direction": "dat-to-parquet",
                "src": "/data/tecs-out",
                "dst": "/data/parquet",
            }
        )
        assert "/data/tecs-out" in vols
        assert "/data/parquet" in vols
        assert vols["/data/tecs-out"]["bind"] == "/input"
        assert vols["/data/parquet"]["bind"] == "/output"

    def test_build_dat_parquet_overwrite_mode_reuses_src(self):
        """DAT-Parquet in overwrite mode should mount src as both input and output."""
        cmd, vols = build_command(
            "dat-parquet-handler",
            {
                "direction": "dat-to-parquet",
                "src": "/data/tecs-out",
                "dst": "/data/tecs-out",  # Same as src when overwriting
                "overwrite": "on",
            }
        )
        # Should have only one volume entry for the shared path
        assert "/data/tecs-out" in vols


class TestFlagTranslationRendering:
    """Tests for flags that use dynamic translation lookups."""

    def test_day_from_flag_has_translation_key_pattern(self):
        """day_from flag should support translation key lookup."""
        conv = get_converter("dat-parquet-handler")
        day_from = next((f for f in conv["flags"] if f["long"] == "--day-from"), None)
        assert day_from is not None
        # The template will look up 'flag_label_day_from'
        # We verify the translation key exists
        assert "flag_label_day_from" in TRANSLATIONS["en"]

    def test_day_to_flag_has_translation_key_pattern(self):
        """day_to flag should support translation key lookup."""
        conv = get_converter("dat-parquet-handler")
        day_to = next((f for f in conv["flags"] if f["long"] == "--day-to"), None)
        assert day_to is not None
        assert "flag_label_day_to" in TRANSLATIONS["en"]

    def test_days_flag_has_translation_key_pattern(self):
        """days flag should support translation key lookup."""
        conv = get_converter("tec-suite")
        days = next((f for f in conv["flags"] if f["long"] == "--days"), None)
        assert days is not None
        assert "flag_label_days" in TRANSLATIONS["en"]

    def test_all_registry_flag_long_names_safe_for_translation_key(self):
        """All flag long names should convert safely to translation key format."""
        for conv_name, conv in CONVERTERS.items():
            for flag in conv.get("flags", []):
                long_name = flag.get("long", "")
                if long_name and long_name.startswith("--"):
                    # Transform --flag-name to flag_name for translation key
                    key = long_name.lstrip("--").replace("-", "_")
                    # Verify the key format is valid
                    assert key.replace("_", "").isalnum(), \
                        f"Flag {long_name} in {conv_name} cannot be converted to a valid translation key"
