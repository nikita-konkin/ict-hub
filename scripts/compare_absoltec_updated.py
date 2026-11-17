"""Compare current AbsolTEC variants against original 30-minute references.

Reads saved original outputs and freshly produced native outputs; computes
per-day, per-coefficient descriptive metrics (RMSE, MAE, bias, maximum
absolute difference, correlation, agreement R2) and DCB differences by
identity, and draws the same graphics as the earlier comparison report:
an overview, per-model VTEC traces with residuals, all-coefficient
differences, and DCB bars. No interpolation and no fitted offsets. Original
all-zero coefficient rows are treated as missing placeholders, matching the
saved evidence conventions in report_absoltec_comparison.py.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.compare_absoltec import COEFFICIENTS, read_results  # noqa: E402
from scripts.validate_absoltec_science import read_dcb  # noqa: E402

UNITS = ["TECU", "TECU/degree", "TECU/degree", "TECU/degree²", "TECU/degree²", "TECU/hour", "TECU/hour²"]
COLORS = ["#1677ac", "#d35d21"]


def _load_original(path: Path) -> np.ndarray:
    original = np.asarray(read_results(path), dtype=float)
    original[np.all(original[:, 1:] == 0, axis=1), 1:] = np.nan
    return original


def _pair(original: np.ndarray, candidate: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Return (original_rows, candidate_rows) on shared epochs."""
    times = {float(row[0]): row for row in candidate}
    left_rows, right_rows = [], []
    for row in original:
        matched = times.get(float(row[0]))
        if matched is None:
            continue
        left_rows.append(row)
        right_rows.append(matched)
    if not left_rows:
        raise ValueError("No shared epochs between original and candidate")
    return np.asarray(left_rows), np.asarray(right_rows)


def _metrics(original: np.ndarray, candidate: np.ndarray) -> dict:
    delta = candidate - original
    valid = np.isfinite(delta)
    rows = []
    for index, name in enumerate(COEFFICIENTS):
        mask = valid[:, index + 1]
        if not np.any(mask):
            rows.append(
                {
                    "coefficient": name,
                    "unit": UNITS[index],
                    "paired_epochs": 0,
                    "rmse": None,
                    "mae": None,
                    "bias": None,
                    "max_abs": None,
                    "correlation": None,
                    "agreement_r2": None,
                }
            )
            continue
        diff = delta[mask, index + 1]
        left, right = original[mask, index + 1], candidate[mask, index + 1]
        centered_left = left - np.mean(left)
        centered_right = right - np.mean(right)
        denominator = np.sqrt(np.sum(centered_left**2) * np.sum(centered_right**2))
        correlation = float(np.sum(centered_left * centered_right) / denominator) if denominator > 0 else None
        ss_res = float(np.sum(diff**2))
        ss_tot = float(np.sum((right - np.mean(right)) ** 2))
        rows.append(
            {
                "coefficient": name,
                "unit": UNITS[index],
                "paired_epochs": int(mask.sum()),
                "rmse": float(np.sqrt(np.mean(diff**2))),
                "mae": float(np.mean(np.abs(diff))),
                "bias": float(np.mean(diff)),
                "max_abs": float(np.max(np.abs(diff))),
                "correlation": correlation,
                "agreement_r2": float(1 - ss_res / ss_tot) if ss_tot > 0 else None,
            }
        )
    return {row["coefficient"]: row for row in rows}


def _dcb_metrics(original_dcb: Path, candidate_dcb: Path) -> dict:
    if not candidate_dcb.exists():
        return {"keys_match": 0, "max_abs": None, "pairs": []}
    left, right = read_dcb(original_dcb), read_dcb(candidate_dcb)
    rows = []
    maximum = 0.0
    matched = 0
    total = 0
    for section in left:
        for key in sorted(set(left[section]) | set(right[section])):
            total += 1
            a, b = left[section].get(key), right[section].get(key)
            if a is None or b is None:
                rows.append({"section": section, "key": key, "difference": None})
                continue
            matched += 1
            rows.append({"section": section, "key": key, "difference": float(b - a)})
            maximum = max(maximum, abs(b - a))
    return {
        "keys_match": matched if matched == total else 0,
        "max_abs": float(maximum) if matched else None,
        "pairs": rows,
    }


def _plots(cases: list[dict], summary: list[dict], dcb_rows: list[dict], output: Path) -> None:
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
    days = [case["day"] for case in cases]
    labels = [f"{d:03d}" for d in days]

    # Overview: legacy-2017 VTEC max difference and solved epochs.
    fig, axes = plt.subplots(1, 2, figsize=(10, 3.6), layout="constrained")
    x = np.arange(len(cases))
    errors, solved_pairs = [], []
    for case in cases:
        row = next(r for r in summary if r["day"] == case["day"] and r["model"] == "legacy-2017")
        errors.append(row["vtec_max_abs"] * 1000 if row["vtec_max_abs"] is not None else np.nan)
        solved_pairs.append((row["original_solved"], row["restored_solved"]))
    axes[0].bar(x, errors, color=COLORS[1], width=0.6)
    axes[0].axhline(1, color="#555555", linestyle="--", label="Tolerance: 1 mTECU")
    for position, value in enumerate(errors):
        if np.isfinite(value):
            axes[0].annotate(f"{value:.1f}", (position, value), xytext=(0, 4), textcoords="offset points", ha="center")
        else:
            axes[0].text(position, 9, "No shared\nepochs", ha="center")
    axes[0].set(title="VTEC maximum absolute difference", ylabel="mTECU (restored versus original)", ylim=(0, 50))
    axes[0].legend(frameon=False, loc="upper right")
    for offset, index, color, label in [
        (-0.18, 0, COLORS[0], "Original"),
        (0.18, 1, COLORS[1], "Restored legacy-2017"),
    ]:
        values = [pair[index] for pair in solved_pairs]
        axes[1].bar(x + offset, values, width=0.34, color=color, label=label)
        for pos, value in zip(x + offset, values, strict=True):
            axes[1].text(pos, value + 0.8, str(value), ha="center", fontsize=9)
    axes[1].set(title="Solved epochs on the same 30-minute grid", ylabel="Epochs (48 expected per day)", ylim=(0, 59))
    axes[1].legend(frameon=False, loc="upper center", ncol=2)
    for ax in axes:
        ax.set_xticks(x, labels)
        ax.set_xlabel("2026 day of year" if days[0] >= 300 else "2025 day of year")
    fig.suptitle("Restored versus original · AKSU · overview (legacy-2017 parity path)")
    fig.savefig(output / "overview.png")
    plt.close(fig)

    # Per-model VTEC traces and residuals.
    for model in ("legacy-2017", "published-updated", "published-old"):
        fig, axes = plt.subplots(len(cases), 2, figsize=(11, 2.2 * len(cases)), layout="constrained")
        axes = np.atleast_2d(axes)
        for case, pair in zip(cases, axes, strict=True):
            entry = case["models"].get(model)
            if entry is None:
                continue
            uts, a, b = entry["uts"], entry["original"], entry["candidate"]
            pair[0].plot(uts, a[:, 1], color=COLORS[0], label="Original", linewidth=1.8)
            pair[0].plot(uts, b[:, 1], "--", color=COLORS[1], label="Restored", linewidth=1.4)
            pair[1].plot(uts, b[:, 1] - a[:, 1], ".-", color=COLORS[1], markersize=3)
            pair[1].axhspan(-0.001, 0.001, color=COLORS[0], alpha=0.18)
            pair[1].axhline(0, color="#555555", linewidth=0.6)
            for ax in pair:
                ax.set_xlim(0, 24)
                ax.set_xticks(range(0, 25, 4))
                ax.set_xlabel("UT (hours)")
            pair[0].set(title=f"Day {case['day']:03d} · VTEC", ylabel="TECU")
            pair[1].set(title="Restored − original (no interpolation)", ylabel="Difference (TECU)")
            if len(uts) < 48:
                pair[0].text(0.97, 0.9, "Partial day; gaps retained", transform=pair[0].transAxes, ha="right")
        axes[0, 0].legend(frameon=False)
        fig.suptitle(f"{model}: same-grid TEC comparison · ±0.001 TECU band on residuals")
        fig.savefig(output / f"vtec-{model}.png")
        plt.close(fig)

    # All seven coefficients, legacy-2017, every day.
    fig, axes = plt.subplots(7, 1, figsize=(11, 16), layout="constrained")
    for index, ax in enumerate(axes):
        for case in cases:
            entry = case["models"].get("legacy-2017")
            if entry is None:
                continue
            ax.plot(
                entry["uts"],
                entry["candidate"][:, index + 1] - entry["original"][:, index + 1],
                label=f"day {case['day']:03d}",
                linewidth=1.1,
            )
        ax.axhspan(-0.001, 0.001, color="#777777", alpha=0.12)
        ax.set(title=COEFFICIENTS[index], ylabel=UNITS[index], xlim=(0, 24), xlabel="UT (hours)")
    axes[0].legend(ncol=5, frameon=False)
    fig.suptitle("Restored legacy-2017 − original · all seven coefficients · gaps retained")
    fig.savefig(output / "coefficient-differences.png")
    plt.close(fig)

    # DCB differences, legacy-2017, per day.
    fig, axes = plt.subplots(len(cases), 1, figsize=(12, 2.4 * len(cases)), layout="constrained")
    axes = np.atleast_1d(axes)
    for case, ax in zip(cases, axes, strict=True):
        rows = [r for r in dcb_rows if r["day"] == case["day"] and r["model"] == "legacy-2017"]
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
            title=f"Day {case['day']:03d}" + (" · partial-day estimates" if case["day"] == 34 else ""),
            ylabel="Difference (TECU)",
        )
    fig.suptitle("DCB: restored legacy-2017 − original · satellite and receiver values")
    fig.savefig(output / "dcb-differences.png")
    plt.close(fig)


def build(original_dir: Path, candidate_roots: dict[str, Path], output: Path, days: list[int]) -> None:
    output.mkdir(parents=True, exist_ok=False)
    metrics_rows, summary_rows, dcb_rows, cases = [], [], [], []
    for day in days:
        original_tec = _load_original(original_dir / f"aksu_{day:03d}_2025.dat")
        original_dcb = original_dir / f"DCB_aksu_{day:03d}_2025.dat"
        case = {"day": day, "models": {}}
        for model, root in candidate_roots.items():
            # Candidate layouts: either YYYY/DDD/site/… or a flat directory.
            tec = root / f"aksu_{day:03d}_2025.dat"
            if not tec.exists():
                tec = root / "2025" / f"{day:03d}" / "aksu" / f"aksu_{day:03d}_2025.dat"
            dcb = tec.with_name(f"DCB_{tec.name}")
            candidate = np.asarray(read_results(tec), dtype=float)
            left, right = _pair(original_tec, candidate)
            per_coefficient = _metrics(left, right)
            for row in per_coefficient.values():
                metrics_rows.append({"day": day, "model": model, **row})
            vtec = per_coefficient["I_v"]
            original_solved = int(np.sum(np.isfinite(original_tec[:, 1])))
            dcb_info = _dcb_metrics(original_dcb, dcb)
            summary_rows.append(
                {
                    "day": day,
                    "model": model,
                    "original_solved": original_solved,
                    "restored_solved": int(np.sum(np.isfinite(candidate[:, 1]))),
                    "shared_epochs": int(np.sum(np.isfinite(left[:, 1]) & np.isfinite(right[:, 1]))),
                    "vtec_rmse": vtec["rmse"],
                    "vtec_mae": vtec["mae"],
                    "vtec_bias": vtec["bias"],
                    "vtec_max_abs": vtec["max_abs"],
                    "vtec_correlation": vtec["correlation"],
                    "vtec_agreement_r2": vtec["agreement_r2"],
                    "dcb_keys_match": dcb_info["keys_match"],
                    "dcb_max_abs": dcb_info["max_abs"],
                }
            )
            for row in dcb_info["pairs"]:
                dcb_rows.append({"day": day, "model": model, **row})
            case["models"][model] = {
                "uts": left[:, 0],
                "original": left,
                "candidate": right,
            }
        cases.append(case)

    def write_csv(name, rows):
        with (output / name).open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)

    write_csv("metrics.csv", metrics_rows)
    write_csv("summary.csv", summary_rows)
    write_csv("dcb.csv", dcb_rows)
    metadata = {
        "generated_at": datetime.now(UTC).isoformat(),
        "days": days,
        "original_dir": str(original_dir.resolve()),
        "candidates": {name: str(root.resolve()) for name, root in candidate_roots.items()},
        "conventions": [
            "Differences are candidate minus original; no interpolation, no fitted offsets.",
            "Original all-zero coefficient rows are missing placeholders.",
            "Shared epochs pair by exact UT; day coverage differences are preserved.",
        ],
    }
    (output / "comparison.json").write_text(json.dumps(metadata, indent=2, allow_nan=False) + "\n", encoding="utf-8")

    _plots(cases, summary_rows, dcb_rows, output)

    lines = [
        "# Updated AbsolTEC versus original — metrics",
        "",
        f"AKSU · 2025/{days[0]:03d}–{days[-1]:03d} · 30-minute grid · generated {metadata['generated_at']}.",
        "",
        "Differences are restored minus original, in TECU, on exactly shared epochs.",
        "Day 034 is partial in both engines. Original all-zero coefficient rows are missing placeholders.",
        "",
        "![Compatibility overview](overview.png)",
        "",
        "## Same-grid comparison",
        "",
        "| Day | Model | Shared epochs | VTEC RMSE | VTEC MAE | VTEC bias | VTEC max abs | VTEC corr | VTEC R² | DCB max abs |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in summary_rows:
        corr = f"{row['vtec_correlation']:.9f}" if row["vtec_correlation"] is not None else ""
        r2 = f"{row['vtec_agreement_r2']:.9f}" if row["vtec_agreement_r2"] is not None else ""
        dcb = f"{row['dcb_max_abs']:.6f}" if row["dcb_max_abs"] is not None else ""
        lines.append(
            f"| {row['day']:03d} | {row['model']} | {row['shared_epochs']} | {row['vtec_rmse']:.6f} | {row['vtec_mae']:.6f} | {row['vtec_bias']:+.6f} | {row['vtec_max_abs']:.6f} | {corr} | {r2} | {dcb} |"
        )
    lines += [
        "",
        "## TEC traces",
        "",
        "The original and restored curves nearly overlap on complete days for the parity path; the residual panels show the remaining differences.",
        "",
        "![legacy-2017 traces and residuals](vtec-legacy-2017.png)",
        "",
        "![published-updated traces and residuals](vtec-published-updated.png)",
        "",
        "![published-old traces and residuals](vtec-published-old.png)",
        "",
        "## All seven coefficients",
        "",
        "![Coefficient residuals](coefficient-differences.png)",
        "",
        "## Satellite and receiver biases",
        "",
        "DCB values are compared by identity, including per-constellation receiver values.",
        "",
        "![Bias differences](dcb-differences.png)",
        "",
        "- [Per-coefficient metrics](metrics.csv) · [day summaries](summary.csv) · [DCB records](dcb.csv) · [metadata](comparison.json)",
        "",
        "Strict 0.001 TECU parity tolerance is a separate, stricter question; these are descriptive metrics.",
        "",
    ]
    (output / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"Saved {output.resolve() / 'REPORT.md'}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--original", type=Path, required=True, help="directory with aksu_0XX_2025.dat originals")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--days", default="032-036", help="comma-separated days or ranges, e.g. 032-036")
    parser.add_argument("--candidate", nargs=2, action="append", metavar=("NAME", "DIR"), required=True)
    args = parser.parse_args()
    days: list[int] = []
    for token in args.days.split(","):
        if "-" in token:
            start, end = (int(part) for part in token.split("-"))
            days.extend(range(start, end + 1))
        else:
            days.append(int(token))
    candidates = {name: Path(path) for name, path in args.candidate}
    build(args.original, candidates, args.output, days)
