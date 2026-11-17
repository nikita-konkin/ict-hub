"""Plot saved round-2 evidence with independently calculated agreement metrics."""

import csv
import hashlib
import io
import json
import zipfile
from pathlib import Path, PureWindowsPath

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs/absoltec_reverse_round2/graphics"
OUT.mkdir(exist_ok=True)
BASE = ROOT / "docs"
old = json.loads((BASE / "absoltec_science_validation.json").read_text())
new = json.loads((BASE / "absoltec_reverse_round2/probe-validation.json").read_text())
names = ["I_v", "G_lon", "G_lat", "G_q_lon", "G_q_lat", "G_t", "G_q_t"]
units = ["TECU", "TECU/degree", "TECU/degree", "TECU/degree²", "TECU/degree²", "TECU/hour", "TECU/hour²"]
colors = ["#1677ac", "#d35d21", "#279169"]
metrics, cases, identities = [], [], []


def read(archive, report, identity, missing_zero=False):
    relative = PureWindowsPath(identity["path"]).relative_to(PureWindowsPath(report["parameters"]["output_dir"]))
    raw = archive.read(relative.as_posix())
    if hashlib.sha256(raw).hexdigest() != identity["sha256"]:
        raise ValueError(f"Evidence hash mismatch: {relative}")
    identities.append({"path": identity["path"], "sha256": identity["sha256"]})
    rows = np.loadtxt(io.BytesIO(raw))
    result = np.full((48, 8), np.nan)
    result[:, 0] = np.arange(48) / 2
    for row in rows:
        index = round(row[0] * 2)
        if not (0 <= index < 48 and abs(index / 2 - row[0]) < 1e-6):
            raise ValueError("Evidence epoch outside requested grid")
        result[index] = row
    if missing_zero:
        result[np.all(result[:, 1:] == 0, axis=1), 1:] = np.nan
    return result


with (
    zipfile.ZipFile(BASE / "absoltec_science_evidence.zip") as a,
    zipfile.ZipFile(BASE / "absoltec_reverse_round2/probe-evidence.zip") as b,
):
    for original_case, probe_case in zip(old["cases"], new["cases"], strict=True):
        day = original_case["day_of_year"]
        original = read(a, old, original_case["legacy"][0]["tec"], True)
        probe = read(b, new, probe_case["legacy"][0]["tec"], True)
        native = read(b, new, probe_case["native"][0]["tec"])
        cases.append((day, original, probe, native))
        for label, reference, candidate in [
            ("restored-original", original, native),
            ("restored-probe", probe, native),
            ("probe-original", original, probe),
        ]:
            for col, name in enumerate(names, 1):
                mask = np.isfinite(reference[:, col]) & np.isfinite(candidate[:, col])
                x, y = reference[mask, col], candidate[mask, col]
                d = y - x
                row = {
                    "day": day,
                    "comparison": label,
                    "coefficient": name,
                    "unit": units[col - 1],
                    "paired_epochs": len(d),
                    "reference_solved": int(np.isfinite(reference[:, col]).sum()),
                    "candidate_solved": int(np.isfinite(candidate[:, col]).sum()),
                    "rmse": float(np.sqrt(np.mean(d * d))),
                    "mae": float(np.mean(abs(d))),
                    "bias": float(np.mean(d)),
                    "max_abs": float(np.max(abs(d))),
                    "correlation": float(np.corrcoef(x, y)[0, 1]) if np.std(x) * np.std(y) > 0 else None,
                    "agreement_r2": float(1 - np.sum(d * d) / np.sum((x - x.mean()) ** 2)) if np.std(x) > 0 else None,
                    "within_tolerance": int(np.sum(abs(d) <= 0.001 + 1e-12)),
                    "full_coverage": bool(mask.all()),
                }
                metrics.append(row)

with (OUT / "metrics.csv").open("w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=list(metrics[0]))
    writer.writeheader()
    writer.writerows(metrics)
(OUT / "metrics.json").write_text(json.dumps({"verified_files": identities, "metrics": metrics}, indent=2) + "\n")
plt.rcParams.update(
    {
        "font.size": 10,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.grid": True,
        "grid.alpha": 0.18,
        "savefig.dpi": 180,
    }
)


def metric(day, label, col="I_v"):
    return next(m for m in metrics if m["day"] == day and m["comparison"] == label and m["coefficient"] == col)


fig, axes = plt.subplots(3, 1, figsize=(8, 9), layout="constrained")
days = [c[0] for c in cases]
x = np.arange(5)
for ax, key, title in zip(
    axes,
    ["rmse", "max_abs", "bias"],
    ["VTEC RMSE", "VTEC maximum absolute error", "VTEC signed mean difference"],
    strict=True,
):
    for offset, label, color, legend in [
        (-0.18, "restored-original", colors[0], "Restored − original"),
        (0.18, "restored-probe", colors[1], "Restored − precision probe"),
    ]:
        values = [metric(day, label)[key] * 1000 for day in days]
        ax.bar(x + offset, values, width=0.35, color=color, label=legend)
        for pos, value in zip(x + offset, values, strict=True):
            ax.annotate(
                f"{value:.2f}",
                (pos, value),
                xytext=(0, 4 if value >= 0 else -12),
                textcoords="offset points",
                ha="center",
                fontsize=8,
            )
    ax.set_xticks(x, [f"{day:03d}" + ("*" if day == 34 else "") for day in days])
    ax.set(title=title, ylabel="mTECU", xlabel="AKSU · 2025 day of year (*partial day, 9 paired epochs)")
    ax.margins(y=0.25)
axes[0].legend(frameon=False, fontsize=9)
fig.savefig(OUT / "metrics-overview.png")
plt.close(fig)

fig, axes = plt.subplots(5, 2, figsize=(12, 14), layout="constrained")
for (day, a, b, c), pair in zip(cases, axes, strict=True):
    for values, color, style, label in [
        (a, colors[0], "-", "Original"),
        (b, colors[2], ":", "Precision probe"),
        (c, colors[1], "--", "Restored"),
    ]:
        pair[0].plot(values[:, 0], values[:, 1], style, color=color, label=label)
    for reference, color, style, label in [
        (a, colors[0], "-", "Restored − original"),
        (b, colors[1], "--", "Restored − probe"),
    ]:
        pair[1].plot(c[:, 0], c[:, 1] - reference[:, 1], style, color=color, label=label)
    pair[1].axhspan(-0.001, 0.001, color="#888888", alpha=0.2)
    m = metric(day, "restored-original")
    n = metric(day, "restored-probe")
    pair[1].set_title(
        f"RMSE {m['rmse']:.6f} → {n['rmse']:.6f} TECU\nMAE {m['mae']:.6f} → {n['mae']:.6f}; n={m['paired_epochs']}",
        fontsize=10,
    )
    pair[0].set_title(f"AKSU 2025/{day:03d}" + (" · partial day" if day == 34 else ""))
    for ax in pair:
        ax.set(xlim=(0, 24), xlabel="UT (hours)", ylabel="TECU")
    if day == 32:
        pair[0].legend(frameon=False, fontsize=9)
        pair[1].legend(frameon=False, fontsize=9)
fig.savefig(OUT / "vtec-round2.png")
plt.close(fig)

fig, axes = plt.subplots(7, 1, figsize=(10, 15), layout="constrained")
for col, ax in enumerate(axes):
    for label, color, offset in [("restored-original", colors[0], -0.18), ("restored-probe", colors[1], 0.18)]:
        ax.bar(
            x + offset, [metric(day, label, names[col])["rmse"] for day in days], width=0.35, color=color, label=label
        )
    ax.set_xticks(x, [f"{day:03d}" + ("*" if day == 34 else "") for day in days])
    ax.set(title=names[col], ylabel=f"RMSE ({units[col]})")
axes[0].legend(frameon=False)
fig.suptitle("All seven coefficients · *day 034 uses only shared finite epochs")
fig.savefig(OUT / "coefficient-metrics.png")
plt.close(fig)

lines = [
    "# Round 2 graphics and metrics",
    "",
    "Comparison of the unchanged original, the isolated precision probe, and restored legacy-2017 model. AKSU 2025/032–036, 30-minute grid. Saved evidence only; no new engine runs.",
    "",
    "![Error metrics](metrics-overview.png)",
    "",
    "![TEC and residuals](vtec-round2.png)",
    "",
    "![Coefficient metrics](coefficient-metrics.png)",
    "",
    "## Metric definitions and limits",
    "",
    "Differences are candidate minus reference. RMSE, MAE, signed bias, maximum absolute difference, Pearson correlation, and agreement R² are provided for all seven coefficients in [metrics.csv](metrics.csv) and [metrics.json](metrics.json). Agreement R² = 1 − sum(error²)/sum((reference − reference mean)²); it is not squared correlation. High correlation does not establish numerical parity.",
    "",
    "The tolerance count uses 0.001 in each coefficient's units with a 1e-12 floating-point boundary allowance. Full-day acceptance also requires complete coverage. Day 034 metrics use only nine paired finite epochs; missing samples are never filled or interpolated. The plotted residual band is ±0.001 TECU. The probe is an experimental binary, not an approved replacement. All input output-file hashes were verified against saved manifests.",
    "",
    "The original prints only three decimal places. Probe differences below that resolution cannot be inferred from these files. No external physical accuracy or speed improvement is established.",
    "",
    "[Round 2 investigation](../REPORT.md)",
]
(OUT / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
print(f"Saved {len(metrics)} metric rows and three figures to {OUT}")
