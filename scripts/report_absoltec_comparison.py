"""Build a portable comparison from hash-verified, previously captured evidence.

This reads saved runs; it neither executes AbsolTEC nor changes hub settings.
"""

# ruff: noqa: E402 -- allow direct CLI invocation from the repository.
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
import zipfile
from datetime import UTC, datetime
from pathlib import Path, PureWindowsPath

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from scripts.compare_absoltec import COEFFICIENTS, compare_arrays, read_results
from scripts.validate_absoltec_science import compare_dcb, read_dcb

UNITS = ["TECU", "TECU/degree", "TECU/degree", "TECU/degree²", "TECU/degree²", "TECU/hour", "TECU/hour²"]
COLORS = ["#1677ac", "#d35d21"]


def write_csv(path, rows):
    if not rows:
        raise ValueError(f"No comparison rows for {path}")
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def build(report_path: Path, archive_path: Path, output: Path):
    report = json.loads(report_path.read_text(encoding="utf-8"))
    output.mkdir(parents=True, exist_ok=False)
    archive_root = PureWindowsPath(report["parameters"]["output_dir"])
    identities = []

    def extract(archive, identity):
        relative = PureWindowsPath(identity["path"]).relative_to(archive_root)
        raw = archive.read(relative.as_posix())
        if len(raw) != identity["bytes"] or hashlib.sha256(raw).hexdigest() != identity["sha256"]:
            raise ValueError(f"Evidence identity mismatch: {relative}")
        path = output / "data" / Path(*relative.parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        identities.append({"file": path.relative_to(output).as_posix(), "sha256": identity["sha256"]})
        return path

    summary, coefficients, observations, biases, timings, cases = [], [], [], [], [], []
    with zipfile.ZipFile(archive_path) as archive:
        for case in report["cases"]:
            day = case["day_of_year"]
            reference = next(run for run in case["legacy"] if run["step_hours"] == 0.5)
            original = np.array(read_results(extract(archive, reference["tec"])))
            reference_dcb = extract(archive, reference["dcb"])
            original[np.all(original[:, 1:] == 0, axis=1), 1:] = np.nan
            # A partial original run can stop writing rows entirely. Reindex
            # onto the requested grid with explicit missing values, never
            # interpolation or zeros. Preserve its actual coverage separately.
            requested_grid = np.arange(0, 24, 0.5)
            aligned = np.full((len(requested_grid), 8), np.nan)
            aligned[:, 0] = requested_grid
            for row in original:
                matches = np.flatnonzero(np.abs(requested_grid - row[0]) <= 1e-6)
                if len(matches) != 1:
                    raise ValueError("Original epoch is outside the requested grid")
                aligned[matches[0]] = row
            original = aligned
            timings.append(
                {
                    "day": day,
                    "engine": "original",
                    "model": "original",
                    "step_hours": 0.5,
                    "seconds": reference["execution"]["process_seconds"],
                    "scope": reference["execution"]["timing_scope"],
                }
            )
            entry = {"day": day, "original": original, "models": {}, "summary": {}}
            for run in case["native"]:
                timings.append(
                    {
                        "day": day,
                        "engine": "restored",
                        "model": run["model"],
                        "step_hours": run["step_hours"],
                        "seconds": run["elapsed_seconds"],
                        "scope": run["timing_scope"],
                    }
                )
                if run["step_hours"] != 0.5:
                    continue
                model = run["model"]
                candidate = np.array(read_results(extract(archive, run["tec"])))
                candidate_dcb = extract(archive, run["dcb"])
                if not np.array_equal(original[:, 0], candidate[:, 0]):
                    raise ValueError("Comparison requires exactly equal grids; interpolation is prohibited")
                comparison = compare_arrays(
                    original.tolist(), candidate.tolist(), atol=0.001, rtol=0, allow_incomplete=True
                )
                dcb = compare_dcb(reference_dcb, candidate_dcb, atol=0.001, rtol=0)
                complete = reference["coverage"]["complete"] and run["coverage"]["complete"]
                baseline = next(c for c in case["comparisons"] if c["model"] == model and c["step_hours"] == 0.5)
                delta = candidate[:, 1:] - original[:, 1:]
                for index, name in enumerate(COEFFICIENTS):
                    valid = np.isfinite(delta[:, index])
                    values = delta[valid, index]
                    rmse = float(np.sqrt(np.mean(values**2))) if len(values) else None
                    maximum = float(np.max(np.abs(values))) if len(values) else None
                    bias = float(np.mean(values)) if len(values) else None
                    # Independent arithmetic must match the existing comparison evidence.
                    if complete:
                        saved = baseline["tec"]["columns"][name]
                        if not np.isclose(rmse, saved["rmse"], atol=1e-11, rtol=1e-10):
                            raise ValueError(f"RMSE disagrees with saved evidence: day {day}, {model}, {name}")
                    coefficients.append(
                        {
                            "day": day,
                            "model": model,
                            "coefficient": name,
                            "unit": UNITS[index],
                            "paired_epochs": len(values),
                            "rmse": rmse,
                            "max_abs_error": maximum,
                            "mean_difference": bias,
                            "exceeds_0p001": int(np.sum(np.abs(values) > 0.001)),
                            "full_day_coverage": complete,
                        }
                    )
                    for row in range(len(original)):
                        a, b = original[row, index + 1], candidate[row, index + 1]
                        observations.append(
                            {
                                "day": day,
                                "model": model,
                                "ut_hours": original[row, 0],
                                "coefficient": name,
                                "original": float(a) if np.isfinite(a) else None,
                                "restored": float(b) if np.isfinite(b) else None,
                                "difference": float(b - a) if np.isfinite(a + b) else None,
                            }
                        )
                a = read_dcb(reference_dcb)
                b = read_dcb(candidate_dcb)
                for section in a:
                    for key in sorted(set(a[section]) | set(b[section])):
                        left, right = a[section].get(key), b[section].get(key)
                        biases.append(
                            {
                                "day": day,
                                "model": model,
                                "section": section,
                                "key": key,
                                "unit": "TECU",
                                "original": left,
                                "restored": right,
                                "difference": right - left if left is not None and right is not None else None,
                            }
                        )
                v = next(
                    c for c in coefficients if c["day"] == day and c["model"] == model and c["coefficient"] == "I_v"
                )
                item = {
                    "day": day,
                    "model": model,
                    "original_solved": reference["coverage"]["solved_epochs"],
                    "restored_solved": run["coverage"]["solved_epochs"],
                    "expected_epochs": 48,
                    "paired_epochs": v["paired_epochs"],
                    "vtec_rmse": v["rmse"],
                    "vtec_max_abs_error": v["max_abs_error"],
                    "dcb_max_abs_error": dcb["max_abs_error"],
                    "dcb_keys_match": dcb["complete"],
                    "strict_acceptance": "FAIL"
                    if not complete or not comparison["passed"] or not dcb["passed"]
                    else "PASS",
                    "full_day_coverage": complete,
                }
                summary.append(item)
                entry["models"][model] = candidate
                entry["summary"][model] = item
            cases.append(entry)

    for name, rows in [
        ("summary", summary),
        ("coefficients", coefficients),
        ("observations", observations),
        ("dcb", biases),
        ("timings", timings),
    ]:
        write_csv(output / f"{name}.csv", rows)
    metadata = {
        "generated_at": datetime.now(UTC).isoformat(),
        "source_validation_acceptance": report["acceptance"],
        "sources": [
            {"path": str(p.resolve()), "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
            for p in (report_path, archive_path)
        ],
        "verified_outputs": identities,
        "summary": summary,
        "coefficients": coefficients,
    }
    (output / "comparison.json").write_text(json.dumps(metadata, indent=2, allow_nan=False) + "\n", encoding="utf-8")

    plt.rcParams.update(
        {
            "font.size": 10,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "savefig.dpi": 180,
            "axes.grid": True,
            "grid.alpha": 0.18,
        }
    )
    fig, axes = plt.subplots(1, 2, figsize=(10, 3.6), layout="constrained")
    x = np.arange(len(cases))
    labels = [f"{c['day']:03d}" for c in cases]
    errors = [
        c["summary"]["legacy-2017"]["vtec_max_abs_error"] * 1000
        if c["summary"]["legacy-2017"]["full_day_coverage"]
        else np.nan
        for c in cases
    ]
    axes[0].bar(x, errors, color=COLORS[1], width=0.6)
    axes[0].axhline(1, color="#555555", linestyle="--", label="Tolerance: 1 mTECU")
    for position, value in enumerate(errors):
        if np.isfinite(value):
            axes[0].annotate(f"{value:.1f}", (position, value), xytext=(0, 4), textcoords="offset points", ha="center")
        else:
            axes[0].text(position, 9, "Partial\nday", ha="center")
    axes[0].set(title="VTEC maximum absolute difference", ylabel="mTECU (restored versus original)", ylim=(0, 50))
    axes[0].legend(frameon=False, loc="upper right")
    for offset, key, color, label in [
        (-0.18, "original_solved", COLORS[0], "Original"),
        (0.18, "restored_solved", COLORS[1], "Restored"),
    ]:
        values = [c["summary"]["legacy-2017"][key] for c in cases]
        axes[1].bar(x + offset, values, width=0.34, color=color, label=label)
        for pos, value in zip(x + offset, values, strict=True):
            axes[1].text(pos, value + 0.8, str(value), ha="center", fontsize=9)
    axes[1].set(title="Solved epochs on the same 30-minute grid", ylabel="Epochs (48 expected per day)", ylim=(0, 59))
    axes[1].legend(frameon=False, loc="upper center", ncol=2)
    for ax in axes:
        ax.set_xticks(x, labels)
        ax.set_xlabel("2025 day of year · AKSU")
    fig.savefig(output / "overview.png")
    plt.close(fig)

    for model in ("legacy-2017", "published"):
        fig, axes = plt.subplots(len(cases), 2, figsize=(11, 12), layout="constrained")
        for case, pair in zip(cases, axes, strict=True):
            a, b = case["original"], case["models"][model]
            pair[0].plot(a[:, 0], a[:, 1], color=COLORS[0], label="Original", linewidth=1.8)
            pair[0].plot(b[:, 0], b[:, 1], "--", color=COLORS[1], label="Restored", linewidth=1.4)
            pair[1].plot(a[:, 0], b[:, 1] - a[:, 1], ".-", color=COLORS[1], markersize=3)
            pair[1].axhspan(-0.001, 0.001, color=COLORS[0], alpha=0.18)
            pair[1].axhline(0, color="#555555", linewidth=0.6)
            for ax in pair:
                ax.set_xlim(0, 24)
                ax.set_xticks(range(0, 25, 4))
                ax.set_xlabel("UT (hours)")
            pair[0].set(title=f"AKSU 2025/{case['day']:03d} · VTEC", ylabel="TECU")
            pair[1].set(title="Restored − original (no interpolation)", ylabel="Difference (TECU)")
            if case["day"] == 34:
                pair[0].text(0.97, 0.9, "Partial day; gaps retained", transform=pair[0].transAxes, ha="right")
        axes[0, 0].legend(frameon=False)
        fig.suptitle(f"{model}: same-grid TEC comparison · ±0.001 TECU band on residuals")
        fig.savefig(output / f"vtec-{model}.png")
        plt.close(fig)

    fig, axes = plt.subplots(7, 1, figsize=(11, 16), layout="constrained")
    for index, ax in enumerate(axes):
        for case in cases:
            ax.plot(
                case["original"][:, 0],
                case["models"]["legacy-2017"][:, index + 1] - case["original"][:, index + 1],
                label=f"2025/{case['day']:03d}",
                linewidth=1.1,
            )
        ax.axhspan(-0.001, 0.001, color="#777777", alpha=0.12)
        ax.set(title=COEFFICIENTS[index], ylabel=UNITS[index], xlim=(0, 24), xlabel="UT (hours)")
    axes[0].legend(ncol=5, frameon=False)
    fig.suptitle("Restored legacy-2017 − original · all seven coefficients · gaps retained")
    fig.savefig(output / "coefficient-differences.png")
    plt.close(fig)

    fig, axes = plt.subplots(len(cases), 1, figsize=(12, 12), layout="constrained")
    for case, ax in zip(cases, axes, strict=True):
        rows = [r for r in biases if r["day"] == case["day"] and r["model"] == "legacy-2017"]
        ax.bar(
            range(len(rows)),
            [r["difference"] if r["difference"] is not None else np.nan for r in rows],
            color=COLORS[1],
        )
        ax.set_xticks(
            range(len(rows)),
            [r["key"] if r["section"] == "satellites" else f"Rec-{r['key']}" for r in rows],
            rotation=90,
            fontsize=8,
        )
        ax.axhspan(-0.001, 0.001, color=COLORS[0], alpha=0.18)
        ax.set(
            title=f"AKSU 2025/{case['day']:03d}" + (" · partial-day estimates" if case["day"] == 34 else ""),
            ylabel="Difference (TECU)",
        )
    fig.suptitle("DCB: restored legacy-2017 − original · satellite and receiver values")
    fig.savefig(output / "dcb-differences.png")
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(9, 4.7))
    fig.subplots_adjust(left=0.10, right=0.98, bottom=0.16, top=0.76)
    for offset, engine, model, color, label in [
        (-0.18, "original", "original", COLORS[0], "Original: child process"),
        (0.18, "restored", "legacy-2017", COLORS[1], "Restored: warm in-process"),
    ]:
        values = [
            next(
                t["seconds"]
                for t in timings
                if t["day"] == c["day"] and t["model"] == model and t["engine"] == engine and t["step_hours"] == 0.5
            )
            for c in cases
        ]
        ax.bar(x + offset, values, width=0.34, label=label, color=color)
    ax.set_xticks(x, labels)
    ax.set(
        xlabel="2025 day of year · AKSU (034 is partial)",
        ylabel="Recorded elapsed seconds",
    )
    ax.legend(frameon=False, loc="lower left", bbox_to_anchor=(0, 1.03), ncol=2)
    fig.suptitle("Saved timings · 30-minute output · different measurement scopes", y=0.97)
    fig.savefig(output / "timings.png")
    plt.close(fig)

    lines = [
        "# Restored versus original AbsolTEC",
        "",
        "AKSU · 2025/032–036 · existing validation evidence from 17 November 2026.",
        "",
        "**Result: strict numerical compatibility fails.** The restored `legacy-2017` mode follows the recovered legacy behavior; `published` is an alternative scientific model and is reported separately. Neither is certified against external ionospheric truth.",
        "",
        "![Compatibility overview](overview.png)",
        "",
        "## Same-grid comparison",
        "",
        "Both engines used identical GPS/GLONASS DAT inputs, a 30-minute output grid, 10° elevation cutoff, and correction coefficient 0.97. All seven coefficients and DCB values were compared without interpolation or fitted offsets. Differences below are restored minus original.",
        "",
        "| Day | Original solved | Restored solved | VTEC RMSE (TECU) | VTEC max difference (TECU) | DCB max difference (TECU) | Strict result |",
        "| --- | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for row in summary:
        if row["model"] != "legacy-2017":
            continue
        suffix = " (partial)" if not row["full_day_coverage"] else ""
        lines.append(
            f"| {row['day']:03d} | {row['original_solved']}/48 | {row['restored_solved']}/48 | {row['vtec_rmse']:.6f}{suffix} | {row['vtec_max_abs_error']:.6f} | {row['dcb_max_abs_error']:.6f} | FAIL |"
        )
    lines += [
        "",
        "The sparse day 034 has different coverage (9 original versus 10 restored epochs); its descriptive TEC errors use only shared finite epochs and cannot establish full-day agreement. Original all-zero coefficient rows are treated as missing placeholders. Native NaN gaps are retained. No gaps are interpolated. Strict tolerance is 0.001 absolute and zero relative tolerance, applied separately to each coefficient's own units and to DCB values; it is not a validated physical-accuracy requirement.",
        "",
        "## TEC traces",
        "",
        "The original and restored curves nearly overlap on complete days; the residual panels show the remaining differences.",
        "",
        "![TEC curves and residuals](vtec-legacy-2017.png)",
        "",
        "## All seven coefficients",
        "",
        "![Coefficient residuals](coefficient-differences.png)",
        "",
        "## Satellite and receiver biases",
        "",
        "DCB values are compared by identity, including per-constellation receiver values. Their separation uses the reporting convention described in the implementation notes; this is not validation against an external hardware-bias catalogue.",
        "",
        "![Bias differences](dcb-differences.png)",
        "",
        "## Timing and stability",
        "",
        "![Saved timings](timings.png)",
        "",
        "The current saved timings do not demonstrate a speed improvement. They are single-run observations with different measurement scopes: original child-process lifetime includes process startup and I/O; native timing includes parsing, fitting and output writing in a process with Python/NumPy/SciPy already loaded. Both exclude input/executable copying. Day 034 is incomplete. These are not repeated, controlled benchmarks; no speedup, peak-memory, or long-run reliability claim is established. The saved original processes exited zero on these five cases, including the incomplete day. The production XP failures are a separate operating environment.",
        "",
        "## Alternative published model",
        "",
        "This mode changes geometry, screening and time conventions. Its differences from the original are expected model differences, not a compatibility result.",
        "",
        "![Published model versus original](vtec-published.png)",
        "",
        "## Data and reproducibility",
        "",
        "- [Day summaries](summary.csv), [coefficient statistics](coefficients.csv), [epoch observations](observations.csv), [DCB records](dcb.csv), and [saved timings](timings.csv). Blank CSV values mean missing evidence, not zero.",
        "- [Comparison metadata](comparison.json) records SHA-256 hashes of the source report, archive, and every extracted TEC/DCB file. Each extracted file was checked against the saved validation identity before plotting.",
        "- Raw half-hour outputs are under `data/`; both native modes and original results are included. All native fine-grid timings are retained in the timing CSV, but no fine-grid original reference was run.",
        "- Complete-day coefficient errors were independently recomputed and checked against the saved validation report. The original executable was not rerun for this report.",
        "- These inputs were derived using a documented RINEX version-header adaptation, not a certified conversion. Only one station and five days are covered. Adjacent-day observations were not loaded. External physical accuracy and production robustness remain unverified.",
        "- [Original validation report](../absoltec_science_validation.json), [input provenance](../absoltec_input_provenance.json), [run manifest](../absoltec_science_manifest.json), and [implementation notes](../absoltec_reconstruction.md).",
        "",
        "Rebuild into a new directory with `python scripts/report_absoltec_comparison.py --output docs/absoltec_comparison_new`.",
        "",
    ]
    (output / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"Saved {output.resolve() / 'REPORT.md'}")
    print(
        f"Verified {len(identities)} TEC/DCB files; {len(coefficients)} coefficient summaries; {len(observations)} paired observation rows."
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, default=ROOT / "docs/absoltec_science_validation.json")
    parser.add_argument("--evidence", type=Path, default=ROOT / "docs/absoltec_science_evidence.zip")
    parser.add_argument("--output", type=Path, default=ROOT / "docs/absoltec_results_comparison")
    args = parser.parse_args()
    build(args.report, args.evidence, args.output)
