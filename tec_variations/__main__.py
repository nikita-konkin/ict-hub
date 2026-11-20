"""Convert tec-suite DAT observations to TEC Variations (dTEC)."""

import argparse
import sys
from pathlib import Path

from .output import MapRenderer, read_contour, write_legacy_map
from .pipeline import DAY_SECONDS, DayReport, collect_day, diagnostics_json, stage_day, write_failure
from .settings import DEFAULTS, OPTION_HELP, parse_selection, parse_settings


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument("--dat-path", required=True, type=Path, help="tec-suite DAT root")
    parser.add_argument("--output-dir", required=True, type=Path, help="Root for YYYY/DDD station tables and maps")
    parser.add_argument("--year", required=True, help="Four-digit input year")
    day = parser.add_mutually_exclusive_group(required=True)
    day.add_argument("--day-of-year", help="One day of year")
    day.add_argument("--days", help="Comma-separated days and inclusive ranges")
    parser.add_argument("--site", help="Comma-separated stations, with all stations selected when omitted")
    for key, help_text in OPTION_HELP.items():
        parser.add_argument("--" + key.replace("_", "-"), default=getattr(DEFAULTS, key), help=help_text)
    parser.add_argument(
        "--previous-day", action=argparse.BooleanOptionalAction, default=True, help="Use previous-day observations"
    )
    parser.add_argument(
        "--next-day", action=argparse.BooleanOptionalAction, default=True, help="Use next-day observations"
    )
    parser.add_argument("--previous-day-root", type=Path, help="Optional separate previous-day DAT root")
    parser.add_argument("--next-day-root", type=Path, help="Optional separate next-day DAT root")
    parser.add_argument("--world-contour", type=Path, help="Optional two-column NiceWorld-180.DAT contour")
    parser.add_argument("--solar-terminator", action="store_true", help="Draw the shell-height solar terminator")
    parser.add_argument("--legacy-maps", action="store_true", help="Write MapTEC-format DAT files for the first band")
    parser.add_argument("--png", action="store_true", help="Draw PNG maps for the first band")
    parser.add_argument("--overwrite", action="store_true", help="Replace the complete directory for each selected day")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run selected days and report any partial or total failures."""
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        settings = parse_settings(vars(args))
        days, sites = parse_selection(args.year, args.day_of_year or "", args.days or "", args.site or "")
        root, output = args.dat_path.resolve(), args.output_dir.resolve()
        previous = (args.previous_day_root or root).resolve() if args.previous_day else None
        following = (args.next_day_root or root).resolve() if args.next_day else None
        for input_root in (root, previous, following):
            if input_root is not None and (
                output.is_relative_to(input_root) or input_root.is_relative_to(output / args.year)
            ):
                raise ValueError("Output root would overlap an input DAT tree")
        contour = read_contour(args.world_contour) if args.png and args.world_contour else None
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    start = round(settings.ut_start * 3600)
    end = min(DAY_SECONDS - 1, round(settings.ut_end * 3600))
    step = settings.map_step_seconds
    first_epoch = ((start + step - 1) // step) * step
    epochs = set(range(first_epoch, end + 1, step)) if args.legacy_maps or args.png else set()
    total = len(days) * len(epochs)
    renderer = MapRenderer(settings, contour, args.solar_terminator) if args.png else None
    failures = 0
    for index, day in enumerate(days):
        report = DayReport(day.isoformat(), epochs=len(epochs))
        offset = index * len(epochs)
        print(f"Processing day {day} ({index + 1}/{len(days)})", flush=True)

        try:
            with stage_day(output, day, args.overwrite) as staging:
                points = collect_day(staging, root, day, sites, epochs, settings, previous, following, report)
                report.maps = len(points)
                report.points = sum(map(len, points.values()))
                for count, (epoch, rows) in enumerate(sorted(points.items()), 1):
                    ut = epoch / 3600
                    stem = f"Map_{day.year:04d}_{day.timetuple().tm_yday:03d}_UT_{ut:8.5f}"
                    if args.legacy_maps:
                        write_legacy_map(staging, stem, ut, rows)
                    if renderer is not None:
                        renderer.write(staging, stem, day, epoch, rows)
                    if count == 1 or count % 10 == 0:
                        print(f"Writing maps {offset + count} / {total}", flush=True)
                (staging / "diagnostics.json").write_text(diagnostics_json(report, settings), encoding="utf-8")
        except (OSError, ValueError) as exc:
            failures += 1
            report.error = str(exc)
        for side, count in (("previous", report.missing_previous), ("next", report.missing_next)):
            if count:
                print(f"Warning {day}: {side} day observations missing for {count} files", flush=True)
        if report.issues:
            print(f"Warning {day}: {len(report.issues)} input issues, first issue: {report.issues[0]}", flush=True)
        if report.error:
            print(f"Error {day}: {report.error}", file=sys.stderr, flush=True)
            try:
                write_failure(output, day, report, settings)
            except OSError as exc:
                print(f"Error {day}: Could not save failure diagnostics — {exc}", file=sys.stderr, flush=True)
        else:
            print(f"{day}: Wrote {report.rows} observations across {len(report.rows_by_station)} stations", flush=True)
            if epochs:
                print(f"{day}: Wrote {report.maps} of {report.epochs} maps with {report.points} points", flush=True)
        print(f"Completed {index + 1} / {len(days)}", flush=True)
    return int(failures > 0)


if __name__ == "__main__":
    raise SystemExit(main())
