"""Command-line entry point for the native MapTEC converter."""

import argparse
import sys
from pathlib import Path

from .output import MapRenderer, read_contour
from .pipeline import DayReport, collect_points, diagnostics_json, publish_day, write_failure
from .settings import DEFAULTS, OPTION_HELP, parse_selection, parse_settings


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument("--dat-path", required=True, type=Path, help="tec-suite DAT root")
    parser.add_argument("--output-dir", required=True, type=Path, help="Root for YYYY/DDD map directories")
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
    parser.add_argument(
        "--overwrite", action="store_true", help="Replace the complete map directory for each selected day"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run selected days and report any partial or total failures."""
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        settings = parse_settings(vars(args))
        days, sites = parse_selection(args.year, args.day_of_year or "", args.days or "", args.site or "")
        root, output = args.dat_path.resolve(), args.output_dir.resolve()
        if output == root or root.is_relative_to(output / args.year):
            raise ValueError("Output root would overlap the input DAT tree")
        contour = read_contour(args.world_contour) if args.world_contour else None
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    previous = (args.previous_day_root or root) if args.previous_day else None
    following = (args.next_day_root or root) if args.next_day else None
    start, end = round(settings.ut_start * 3600), round(settings.ut_end * 3600)
    step = settings.map_step_seconds
    first_epoch = ((start + step - 1) // step) * step
    epochs = set(range(first_epoch, end + 1, step))
    total = len(days) * len(epochs)
    renderer = MapRenderer(settings, contour, args.solar_terminator)
    failures = 0
    for index, day in enumerate(days):
        report = DayReport(day.isoformat(), epochs=len(epochs))
        offset = index * len(epochs)
        print(f"Processing day {day} ({index + 1}/{len(days)})", flush=True)

        def progress(count: int, initial: int = offset) -> None:
            if count == 1 or count % 10 == 0:
                print(f"Writing maps {initial + count} / {total}", flush=True)

        try:
            points = collect_points(root, day, sites, epochs, settings, previous, following, report)
            report.maps = len(points)
            report.points = sum(map(len, points.values()))
            if not points:
                raise ValueError("Nothing to map for the selected stations, arcs and epochs")
            publish_day(output, day, points, diagnostics_json(report, settings), renderer, args.overwrite, progress)
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
            print(f"{day}: Wrote {report.maps} of {report.epochs} maps with {report.points} points", flush=True)
        print(f"Completed {index + 1} / {len(days)}", flush=True)
    return int(failures > 0)


if __name__ == "__main__":
    raise SystemExit(main())
