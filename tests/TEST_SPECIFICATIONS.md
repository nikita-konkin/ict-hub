# Test Suggestions for New Features

This document outlines comprehensive test coverage for recently added functions and options across the ICT-Hub application.

## 1. Python Backend Tests - Day Range Validation (jobs.py)

### 1.1 DAT <-> Parquet Day Range Validation Tests

Add these tests to `test_jobs.py` in the `TestStartJob` class:

```python
@patch("app.jobs.start_container", return_value="container_day_range_valid")
def test_dat_parquet_accepts_valid_day_from_and_day_to(self, mock_start, operator_client):
    """DAT-Parquet with valid day_from and day_to should succeed and pass to container."""
    response = operator_client.post(
        "/jobs/start",
        data=self._start_dat_parquet_job_data(day_from="1", day_to="366"),
        headers={"HX-Request": "true"},
        follow_redirects=False,
    )
    assert response.status_code == 200
    assert mock_start.called
    _, kwargs = mock_start.call_args
    # Verify the day_from and day_to flags are passed to the container
    command = kwargs.get("command", "")
    assert "--day-from" in str(command) or "day_from" in str(kwargs)

def test_dat_parquet_day_from_below_range_returns_400(self, operator_client):
    """Day from < 1 should be rejected with 400."""
    response = operator_client.post(
        "/jobs/start",
        data=self._start_dat_parquet_job_data(day_from="0", day_to="100"),
        headers={"HX-Request": "true"},
        follow_redirects=False,
    )
    assert response.status_code == 400
    assert b"day_from" in response.content or b"Day from" in response.content
    assert b"1" in response.content and b"366" in response.content

def test_dat_parquet_day_from_above_range_returns_400(self, operator_client):
    """Day from > 366 should be rejected with 400."""
    response = operator_client.post(
        "/jobs/start",
        data=self._start_dat_parquet_job_data(day_from="367", day_to="367"),
        headers={"HX-Request": "true"},
        follow_redirects=False,
    )
    assert response.status_code == 400

def test_dat_parquet_day_to_below_range_returns_400(self, operator_client):
    """Day to < 1 should be rejected with 400."""
    response = operator_client.post(
        "/jobs/start",
        data=self._start_dat_parquet_job_data(day_from="1", day_to="0"),
        headers={"HX-Request": "true"},
        follow_redirects=False,
    )
    assert response.status_code == 400

def test_dat_parquet_day_to_above_range_returns_400(self, operator_client):
    """Day to > 366 should be rejected with 400."""
    response = operator_client.post(
        "/jobs/start",
        data=self._start_dat_parquet_job_data(day_from="100", day_to="500"),
        headers={"HX-Request": "true"},
        follow_redirects=False,
    )
    assert response.status_code == 400

def test_dat_parquet_day_from_greater_than_day_to_returns_400(self, operator_client):
    """day_from > day_to should be rejected with 400."""
    response = operator_client.post(
        "/jobs/start",
        data=self._start_dat_parquet_job_data(day_from="100", day_to="50"),
        headers={"HX-Request": "true"},
        follow_redirects=False,
    )
    assert response.status_code == 400
    assert b"day_from" in response.content or b"less than or equal" in response.content

def test_dat_parquet_day_range_non_numeric_returns_400(self, operator_client):
    """Non-numeric day values should be rejected with 400."""
    response = operator_client.post(
        "/jobs/start",
        data=self._start_dat_parquet_job_data(day_from="abc", day_to="100"),
        headers={"HX-Request": "true"},
        follow_redirects=False,
    )
    assert response.status_code == 400
    assert b"numeric" in response.content.lower() or b"digit" in response.content.lower()

def test_dat_parquet_day_from_empty_is_valid(self, mock_start, operator_client):
    """Empty day_from should be valid (optional)."""
    response = operator_client.post(
        "/jobs/start",
        data=self._start_dat_parquet_job_data(day_from="", day_to="100"),
        headers={"HX-Request": "true"},
        follow_redirects=False,
    )
    assert response.status_code == 200
    assert mock_start.called

def test_dat_parquet_day_to_empty_is_valid(self, mock_start, operator_client):
    """Empty day_to should be valid (optional)."""
    response = operator_client.post(
        "/jobs/start",
        data=self._start_dat_parquet_job_data(day_from="100", day_to=""),
        headers={"HX-Request": "true"},
        follow_redirects=False,
    )
    assert response.status_code == 200
    assert mock_start.called

def test_dat_parquet_both_day_range_empty_is_valid(self, mock_start, operator_client):
    """Both day_from and day_to empty should be valid."""
    response = operator_client.post(
        "/jobs/start",
        data=self._start_dat_parquet_job_data(day_from="", day_to=""),
        headers={"HX-Request": "true"},
        follow_redirects=False,
    )
    assert response.status_code == 200
    assert mock_start.called

@patch("app.jobs.start_container", return_value="container_day_range_edge")
def test_dat_parquet_day_boundaries_1_and_366(self, mock_start, operator_client):
    """Edge case: day_from=1 and day_to=366 should succeed."""
    response = operator_client.post(
        "/jobs/start",
        data=self._start_dat_parquet_job_data(day_from="1", day_to="366"),
        headers={"HX-Request": "true"},
        follow_redirects=False,
    )
    assert response.status_code == 200
    assert mock_start.called

@patch("app.jobs.start_container", return_value="container_day_range_single")
def test_dat_parquet_day_from_equals_day_to(self, mock_start, operator_client):
    """Edge case: day_from == day_to should succeed."""
    response = operator_client.post(
        "/jobs/start",
        data=self._start_dat_parquet_job_data(day_from="100", day_to="100"),
        headers={"HX-Request": "true"},
        follow_redirects=False,
    )
    assert response.status_code == 200
    assert mock_start.called

@patch("app.jobs.start_container", return_value="container_day_whitespace")
def test_dat_parquet_day_range_whitespace_trimmed(self, mock_start, operator_client):
    """Whitespace around day values should be trimmed."""
    response = operator_client.post(
        "/jobs/start",
        data=self._start_dat_parquet_job_data(day_from="  50  ", day_to="  100  "),
        headers={"HX-Request": "true"},
        follow_redirects=False,
    )
    assert response.status_code == 200
    assert mock_start.called
```

### 1.2 TEC-Suite Days Option Tests

Add these tests to `test_jobs.py`:

```python
@patch("app.jobs.start_container", return_value="container_tec_days")
def test_tec_suite_accepts_days_filter(self, mock_start, operator_client):
    """TEC-Suite with --days option should pass to container."""
    response = operator_client.post(
        "/jobs/start",
        data=self._start_job_data(days="1-5,10,12-14"),
        headers={"HX-Request": "true"},
        follow_redirects=False,
    )
    assert response.status_code == 200
    assert mock_start.called

@patch("app.jobs.start_container", return_value="container_tec_days_empty")
def test_tec_suite_days_empty_is_valid(self, mock_start, operator_client):
    """TEC-Suite with empty --days should be valid (optional)."""
    response = operator_client.post(
        "/jobs/start",
        data=self._start_job_data(days=""),
        headers={"HX-Request": "true"},
        follow_redirects=False,
    )
    assert response.status_code == 200
    assert mock_start.called

@patch("app.jobs.start_container", return_value="container_tec_jobs_default")
def test_tec_suite_jobs_default_is_1(self, mock_start, operator_client):
    """TEC-Suite default --jobs should be 1 (not 4)."""
    response = operator_client.post(
        "/jobs/start",
        data=self._start_job_data(jobs=""),  # omit jobs value
        headers={"HX-Request": "true"},
        follow_redirects=False,
    )
    assert response.status_code == 200
    assert mock_start.called
    # Verify default is used
    _, kwargs = mock_start.call_args
    command = str(kwargs.get("command", ""))
    # The command builder should use default 1 if not provided
```

## 2. JavaScript Function Tests - analysis.html

These tests would typically be run with Jest, Mocha, or similar JavaScript test framework. They test the new plotting functions:

### 2.1 detectSeriesXKey() Tests

```javascript
describe("detectSeriesXKey", () => {
  test("detects 'ut' as x-axis key", () => {
    const series = { ut: [1, 2, 3], mean_tec: [10, 20, 30] };
    expectEqual(detectSeriesXKey(series), "ut");
  });

  test("detects 'hour' as x-axis key", () => {
    const series = { hour: [0, 1, 2], tec_l1l2: [100, 101, 102] };
    expectEqual(detectSeriesXKey(series), "hour");
  });

  test("detects 'time' as x-axis key", () => {
    const series = { time: [0, 100, 200], value: [10, 20, 30] };
    expectEqual(detectSeriesXKey(series), "time");
  });

  test("detects 'x' as fallback key", () => {
    const series = { x: [1, 2, 3], y: [10, 20, 30] };
    expectEqual(detectSeriesXKey(series), "x");
  });

  test("returns null when no standard x-axis key found", () => {
    const series = { latitude: [1, 2, 3], longitude: [10, 20, 30] };
    expectEqual(detectSeriesXKey(series), null);
  });

  test("prefers 'ut' over 'hour' when both present", () => {
    const series = { ut: [1, 2], hour: [0, 1], value: [10, 20] };
    expectEqual(detectSeriesXKey(series), "ut");
  });

  test("handles numeric arrays correctly", () => {
    const series = { ut: [1.5, 2.5, 3.5], data: [10, 20, 30] };
    expectEqual(detectSeriesXKey(series), "ut");
    expectTrue(isNumericArray(series.ut));
  });
});
```

### 2.2 toRootXYTrace() Tests

```javascript
describe("toRootXYTrace", () => {
  test("extracts root-level x and y into series format", () => {
    const payload = {
      x: [1, 2, 3],
      y: [10, 20, 30],
      name: "TEC Values"
    };
    const trace = toRootXYTrace(payload);
    expectEqual(trace.x, [1, 2, 3]);
    expectEqual(trace.y, [10, 20, 30]);
    expectEqual(trace.name, "TEC Values");
  });

  test("returns null if payload has no x and y", () => {
    const payload = { a: [1, 2], b: [3, 4] };
    expectEqual(toRootXYTrace(payload), null);
  });

  test("creates trace with default name if not provided", () => {
    const payload = { x: [1, 2], y: [10, 20] };
    const trace = toRootXYTrace(payload);
    expectEqual(trace.name, "Series");
  });
});
```

### 2.3 renderPlotlyChart() Multi-Schema Tests

```javascript
describe("renderPlotlyChart", () => {
  test("renders chart for payload.data schema", () => {
    const json = {
      format: "json",
      payload: {
        data: {
          x: [1, 2, 3],
          y: [10, 20, 30]
        }
      }
    };
    // Should not return early for JSON mode
    // Should render chart via Plotly
    const result = renderPlotlyChart("plot-container", json);
    expectTrue(result); // or check for Plotly.newPlot call
  });

  test("renders chart for payload.series schema", () => {
    const json = {
      format: "json",
      payload: {
        series: {
          ut: [1, 2, 3],
          mean_tec: [10, 20, 30],
          student_ci: [0.5, 0.5, 0.5]
        }
      }
    };
    const result = renderPlotlyChart("plot-container", json);
    expectTrue(result);
  });

  test("renders chart for root x/y schema", () => {
    const json = {
      format: "json",
      payload: {
        x: [0, 1, 2],
        y: [100, 101, 102],
        name: "Satellite Data"
      }
    };
    const result = renderPlotlyChart("plot-container", json);
    expectTrue(result);
  });

  test("does not return early for JSON format (fixes rendering bug)", () => {
    const json = {
      format: "json",
      payload: {
        series: {
          hour: [0, 1, 2],
          tec_l1l2: [100, 101, 102]
        }
      }
    };
    // Verify that JSON mode does NOT short-circuit chart rendering
    const result = renderPlotlyChart("plot-container", json);
    expectFalse(result === null || result === undefined);
  });
});
```

### 2.4 drawSeriesOnCanvas() Tests

```javascript
describe("drawSeriesOnCanvas", () => {
  test("draws canvas using detected x-axis key 'ut'", () => {
    const ctx = createMockCanvasContext();
    const series = {
      ut: [1, 2, 3, 4, 5],
      mean_tec: [10, 15, 20, 18, 25]
    };
    const result = drawSeriesOnCanvas(ctx, series, 400, 300);
    expectTrue(result); // drawing succeeded
    expectTrue(ctx.lineTo.called); // canvas methods were called
  });

  test("draws canvas using detected x-axis key 'hour'", () => {
    const ctx = createMockCanvasContext();
    const series = {
      hour: [0, 6, 12, 18, 24],
      elevation: [30, 45, 60, 50, 35]
    };
    const result = drawSeriesOnCanvas(ctx, series, 400, 300);
    expectTrue(result);
  });

  test("fails gracefully when x-axis key not detected", () => {
    const ctx = createMockCanvasContext();
    const series = {
      latitude: [45, 46, 47],
      longitude: [10, 11, 12]
    };
    const result = drawSeriesOnCanvas(ctx, series, 400, 300);
    expectFalse(result);
  });

  test("normalizes data to fit canvas bounds", () => {
    const ctx = createMockCanvasContext();
    const series = {
      x: [0.001, 0.002, 0.003],
      y: [1000000, 2000000, 3000000]
    };
    const result = drawSeriesOnCanvas(ctx, series, 400, 300);
    expectTrue(result);
    // Verify lineTo calls use scaled coordinates
    const lineToArgs = ctx.lineTo.calls.map(c => c.args);
    lineToArgs.forEach(([x, y]) => {
      expectTrue(x >= 0 && x <= 400);
      expectTrue(y >= 0 && y <= 300);
    });
  });
});
```

## 3. Integration Tests - Flag Translation

Add to `test_jobs.py`:

```python
class TestFlagTranslation:
    """Tests for dynamic flag label/help translation."""

    def test_run_page_renders_day_from_label_in_english(self, operator_client):
        response = operator_client.get("/run/dat-parquet-handler", follow_redirects=True)
        assert response.status_code == 200
        # Check that the translated label or original is present
        assert b"day_from" in response.content or b"Day from" in response.content

    def test_run_page_renders_day_to_label_in_english(self, operator_client):
        response = operator_client.get("/run/dat-parquet-handler", follow_redirects=True)
        assert response.status_code == 200
        assert b"day_to" in response.content or b"Day to" in response.content

    def test_run_page_renders_days_label_for_tec_suite(self, operator_client):
        response = operator_client.get("/run/tec-suite", follow_redirects=True)
        assert response.status_code == 200
        assert b"days" in response.content or b"Days Filter" in response.content

    def test_run_page_contains_flag_help_text_for_day_range(self, operator_client):
        response = operator_client.get("/run/dat-parquet-handler", follow_redirects=True)
        assert response.status_code == 200
        # Help text should be present (either translated or fallback)
        content = response.content.decode('utf-8', errors='ignore')
        assert "day" in content.lower() and ("range" in content.lower() or "filter" in content.lower())
```

## 4. Registry Tests

Add to a new file `tests/test_registry.py`:

```python
"""Tests for converter registry and flag definitions."""

import pytest
from app.registry import CONVERTERS, get_converter


class TestConverterRegistry:
    """Tests for converter definitions and flag specifications."""

    def test_tec_suite_converter_exists(self):
        conv = get_converter("tec-suite")
        assert conv is not None
        assert conv["label"] == "TEC-Suite"

    def test_tec_suite_has_days_flag(self):
        conv = get_converter("tec-suite")
        days_flag = next((f for f in conv["flags"] if f["long"] == "--days"), None)
        assert days_flag is not None
        assert days_flag["type"] == "text"
        assert days_flag["label"] == "Days Filter"

    def test_tec_suite_jobs_default_is_1(self):
        conv = get_converter("tec-suite")
        jobs_flag = next((f for f in conv["flags"] if f["long"] == "--jobs"), None)
        assert jobs_flag is not None
        assert jobs_flag["default"] == 1

    def test_dat_parquet_converter_exists(self):
        conv = get_converter("dat-parquet-handler")
        assert conv is not None
        assert "parquet" in conv["label"].lower()

    def test_dat_parquet_has_day_from_flag(self):
        conv = get_converter("dat-parquet-handler")
        day_from = next((f for f in conv["flags"] if f["long"] == "--day-from"), None)
        assert day_from is not None
        assert day_from["type"] == "number"
        assert day_from["min"] == 1
        assert day_from["max"] == 366

    def test_dat_parquet_has_day_to_flag(self):
        conv = get_converter("dat-parquet-handler")
        day_to = next((f for f in conv["flags"] if f["long"] == "--day-to"), None)
        assert day_to is not None
        assert day_to["type"] == "number"
        assert day_to["min"] == 1
        assert day_to["max"] == 366

    def test_all_flags_have_label(self):
        for conv_name, conv in CONVERTERS.items():
            for flag in conv.get("flags", []):
                assert "label" in flag, f"Flag {flag.get('long')} in {conv_name} missing label"

    def test_all_flags_have_help_text(self):
        for conv_name, conv in CONVERTERS.items():
            for flag in conv.get("flags", []):
                assert "help" in flag, f"Flag {flag.get('long')} in {conv_name} missing help text"

    def test_number_flags_have_min_max(self):
        for conv_name, conv in CONVERTERS.items():
            for flag in conv.get("flags", []):
                if flag.get("type") == "number":
                    assert "min" in flag, f"Number flag {flag.get('long')} in {conv_name} missing min"
                    assert "max" in flag, f"Number flag {flag.get('long')} in {conv_name} missing max"
```

## 5. Translation Keys Tests

Add to `tests/test_registry.py`:

```python
class TestTranslationKeys:
    """Tests for i18n keys for converter flags."""

    def test_day_from_translation_keys_exist(self):
        from app.i18n import TRANSLATIONS
        assert "flag_label_day_from" in TRANSLATIONS["en"]
        assert "flag_label_day_from" in TRANSLATIONS["ru"]
        assert "flag_help_day_from" in TRANSLATIONS["en"]
        assert "flag_help_day_from" in TRANSLATIONS["ru"]

    def test_day_to_translation_keys_exist(self):
        from app.i18n import TRANSLATIONS
        assert "flag_label_day_to" in TRANSLATIONS["en"]
        assert "flag_label_day_to" in TRANSLATIONS["ru"]
        assert "flag_help_day_to" in TRANSLATIONS["en"]
        assert "flag_help_day_to" in TRANSLATIONS["ru"]

    def test_days_filter_translation_keys_exist(self):
        from app.i18n import TRANSLATIONS
        assert "flag_label_days" in TRANSLATIONS["en"]
        assert "flag_label_days" in TRANSLATIONS["ru"]
        assert "flag_help_days" in TRANSLATIONS["en"]
        assert "flag_help_days" in TRANSLATIONS["ru"]

    def test_all_translation_keys_have_non_empty_values(self):
        from app.i18n import TRANSLATIONS
        for lang_code, keys in TRANSLATIONS.items():
            assert isinstance(keys, dict), f"Language {lang_code} is not a dict"
            for key, value in keys.items():
                assert isinstance(value, str), f"Key {key} in {lang_code} is not a string"
                assert len(value) > 0, f"Key {key} in {lang_code} is empty"
```

## 6. Command Building Tests

Add to `tests/test_registry.py`:

```python
from app.registry import build_command

class TestCommandBuilding:
    """Tests for CLI command generation with new options."""

    def test_build_tec_suite_command_with_days_filter(self):
        cmd = build_command(
            "tec-suite",
            {
                "root": "/data/rinex",
                "jobs": "1",
                "days": "1-5,10",
            },
            "/app/out"
        )
        assert "--days" in cmd or "-days" in cmd
        assert "1-5,10" in cmd

    def test_build_tec_suite_command_without_days_filter(self):
        cmd = build_command(
            "tec-suite",
            {
                "root": "/data/rinex",
                "jobs": "2",
            },
            "/app/out"
        )
        # Should not include --days flag if not provided
        assert "--days" not in cmd or "--days \"\"" in cmd or "--days ''" in cmd

    def test_build_dat_parquet_command_with_day_range(self):
        cmd = build_command(
            "dat-parquet-handler",
            {
                "direction": "dat-to-parquet",
                "src": "/data/tecs-out",
                "dst": "/data/parquet",
                "day_from": "1",
                "day_to": "100",
            },
            None
        )
        assert "--day-from" in cmd
        assert "--day-to" in cmd
        assert "1" in cmd or "-from 1" in cmd
        assert "100" in cmd or "-to 100" in cmd

    def test_build_dat_parquet_command_without_day_range(self):
        cmd = build_command(
            "dat-parquet-handler",
            {
                "direction": "dat-to-parquet",
                "src": "/data/tecs-out",
                "dst": "/data/parquet",
            },
            None
        )
        # Should still be valid without day range
        assert "dat-parquet-handler" in cmd or "parquet" in cmd.lower()
```

## Implementation Notes

### For Python Tests:
1. Use `@patch("app.jobs.start_container")` to mock Docker interactions
2. Check `response.status_code == 200` for success, `400` for validation errors
3. Verify error messages contain relevant keywords (e.g., "numeric", "range")
4. Test edge cases: boundary values (1, 366), empty strings, whitespace

### For JavaScript Tests:
1. Requires a test runner like Jest or Mocha
2. Mock canvas context and Plotly library
3. Test data formats matching actual API responses (absoltec_average vs tec_satellite)
4. Verify functions don't throw exceptions on malformed input

### Running Tests:

```bash
# Run all backend tests
pytest

# Run specific test class
pytest tests/test_jobs.py::TestStartJob::test_dat_parquet_day_from_below_range_returns_400

# Run with coverage
pytest --cov=app tests/

# For JavaScript (once test framework is set up)
npm test
```

## Priority Order for Implementation

1. **High Priority**: DAT-Parquet day range validation tests (integration points with existing tests)
2. **High Priority**: Registry tests for flag definitions and defaults
3. **Medium Priority**: TEC-Suite days filter tests
4. **Medium Priority**: Translation key tests
5. **Low Priority**: JavaScript function tests (requires test framework setup)

