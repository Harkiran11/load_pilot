"""Validate historical freight CSV data without modifying or dropping records."""

import argparse
import csv
import hashlib
import json
import math
from collections import Counter
from datetime import date, datetime
from pathlib import Path

from loadpilot_model.paths import PROJECT_ROOT

SCHEMA = {
    "shipment_id": "identifier",
    "lane_id": "lane identifier",
    "origin_city": "city",
    "origin_state": "state code",
    "origin_latitude": "degrees [-90, 90]",
    "origin_longitude": "degrees [-180, 180]",
    "destination_city": "city",
    "destination_state": "state code",
    "destination_latitude": "degrees [-90, 90]",
    "destination_longitude": "degrees [-180, 180]",
    "distance_miles": "positive loaded miles",
    "weight_lbs": "positive pounds",
    "order_type": "fixed_rate or open_bid",
    "offered_rate": "positive total currency amount (source confirmation pending)",
    "bid_rate": "positive total currency amount for open bids (source confirmation pending)",
    "won": "true or false award outcome",
    "cost_per_mile": "nonnegative currency/mile (source confirmation pending)",
    "estimated_cost": "nonnegative currency amount (source confirmation pending)",
    "margin_estimate": "signed estimated currency margin (source confirmation pending)",
    "shipped_at": "YYYY-MM-DD HH:MM:SS; timezone unspecified",
    "market_rpm": "positive currency/mile (source confirmation pending)",
    "all_in_rpm": "positive currency/mile (source confirmation pending)",
    "fuel_surcharge_rpm": "nonnegative currency/mile (source confirmation pending)",
    "season": "spring, summer, fall, or winter",
    "booked_lead_hours": "nonnegative hours",
}
OPTIONAL = {"margin_estimate", "all_in_rpm", "fuel_surcharge_rpm", "booked_lead_hours"}
POSITIVE = {"distance_miles", "weight_lbs", "offered_rate", "bid_rate", "market_rpm", "all_in_rpm"}
NONNEGATIVE = {"cost_per_mile", "estimated_cost", "fuel_surcharge_rpm", "booked_lead_hours"}
COORDINATES = {
    "origin_latitude": 90,
    "destination_latitude": 90,
    "origin_longitude": 180,
    "destination_longitude": 180,
}
NUMERIC = POSITIVE | NONNEGATIVE | set(COORDINATES) | {"margin_estimate"}
MISSING = {"", "na", "n/a", "null", "none", "nan"}


def validate(path: Path, as_of: date, expected_rows: int | None = None) -> dict:
    issues = []

    def issue(severity: str, code: str, rows: list[int], detail: str) -> None:
        issues.append(
            {
                "severity": severity,
                "code": code,
                "count": len(rows),
                "sample_csv_lines": rows[:10],
                "detail": detail,
            }
        )

    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.reader(stream, strict=True)
        header = next(reader, [])
        raw_rows = []
        for values in reader:
            raw_rows.append((reader.line_num, values))
    duplicates = [name for name, count in Counter(header).items() if count > 1]
    missing_columns = sorted(set(SCHEMA) - OPTIONAL - set(header))
    if duplicates or missing_columns or not header:
        issue(
            "error",
            "INVALID_SCHEMA",
            [],
            f"Missing required columns: {missing_columns}; duplicate headers: {duplicates}",
        )
    for name in sorted(OPTIONAL - set(header)):
        issue("warning", "OPTIONAL_COLUMN_ABSENT", [], name)
    malformed = [line for line, values in raw_rows if len(values) != len(header)]
    if malformed:
        issue("error", "MALFORMED_ROW", malformed, "Field count differs from header")
    records = [
        (line, dict(zip(header, values))) for line, values in raw_rows if len(values) == len(header)
    ]
    if not raw_rows:
        issue("error", "EMPTY_DATASET", [], "No data records")
    if expected_rows is not None and len(raw_rows) != expected_rows:
        issue("error", "ROW_COUNT_MISMATCH", [], f"Expected {expected_rows}, got {len(raw_rows)}")

    violations = {}
    missing_counts = Counter()
    numeric_values = {name: [] for name in NUMERIC}
    dates = []
    ids = {}
    seen_records = {}
    order_counts = Counter()
    eligible = 0
    cost_matches = market_matches = reference_matches = 0

    def fail(code: str, line: int) -> None:
        violations.setdefault(code, []).append(line)

    for line, record in records:
        clean = {name: value.strip() for name, value in record.items()}
        for name in ("order_type", "won", "season"):
            if name in clean:
                clean[name] = clean[name].lower()
        missing = {name for name in SCHEMA if clean.get(name, "").lower() in MISSING}
        missing_counts.update(missing)
        required = set(SCHEMA) - OPTIONAL - {"bid_rate"}
        if clean.get("order_type") == "open_bid":
            required.add("bid_rate")
        for name in sorted(missing & required & set(header)):
            fail(f"MISSING_REQUIRED:{name}", line)
        numbers = {}
        for name in sorted(NUMERIC & set(header) - missing):
            try:
                number = float(clean[name])
            except ValueError:
                fail(f"NONNUMERIC:{name}", line)
                continue
            if not math.isfinite(number):
                fail(f"NONFINITE:{name}", line)
                continue
            numbers[name] = number
            numeric_values[name].append(number)
            if (
                name in POSITIVE
                and number <= 0
                or name in NONNEGATIVE
                and number < 0
                or name in COORDINATES
                and abs(number) > COORDINATES[name]
            ):
                fail(f"OUT_OF_RANGE:{name}", line)
        for name, allowed in {
            "order_type": {"fixed_rate", "open_bid"},
            "won": {"true", "false"},
            "season": {"spring", "summer", "fall", "winter"},
        }.items():
            if name in clean and name not in missing and clean[name].lower() not in allowed:
                fail(f"INVALID_CATEGORY:{name}", line)
        order_counts[clean.get("order_type", "<missing>")] += 1
        if "shipped_at" in clean and "shipped_at" not in missing:
            try:
                stamp = datetime.strptime(clean["shipped_at"], "%Y-%m-%d %H:%M:%S")
                dates.append(stamp)
                if stamp.date() > as_of:
                    fail("FUTURE_SHIPMENT_DATE", line)
            except ValueError:
                fail("INVALID_DATE", line)
        shipment_id = clean.get("shipment_id")
        if shipment_id and "shipment_id" not in missing:
            ids.setdefault(shipment_id, []).append(line)
        fingerprint = tuple(clean.get(name, "") for name in header)
        seen_records.setdefault(fingerprint, []).append(line)
        distance = numbers.get("distance_miles", 0)
        if clean.get("order_type") == "open_bid" and distance > 0:
            eligible += numbers.get("bid_rate", 0) > 0
        if {"estimated_cost", "cost_per_mile", "distance_miles"} <= numbers.keys():
            cost_matches += (
                abs(numbers["estimated_cost"] - distance * numbers["cost_per_mile"]) <= 0.01
            )
        if {"market_rpm", "all_in_rpm"} <= numbers.keys():
            market_matches += abs(numbers["market_rpm"] - numbers["all_in_rpm"]) <= 1e-9
        if distance > 0 and {"offered_rate", "market_rpm"} <= numbers.keys():
            reference_matches += (
                abs(numbers["offered_rate"] / distance - numbers["market_rpm"]) <= 0.001
            )

    duplicate_ids = {key: lines for key, lines in ids.items() if len(lines) > 1}
    duplicate_lines = [line for lines in seen_records.values() for line in lines[1:]]
    if duplicate_ids:
        issue(
            "error",
            "DUPLICATE_SHIPMENT_ID",
            [line for lines in duplicate_ids.values() for line in lines],
            "Repeated shipment IDs",
        )
    if duplicate_lines:
        issue(
            "error",
            "DUPLICATE_RECORD",
            duplicate_lines,
            "Repeated records after whitespace stripping",
        )
    for code, lines in sorted(violations.items()):
        issue("error", code, lines, code)
    fully_missing = sorted(
        name
        for name in header
        if name in SCHEMA and records and missing_counts[name] == len(records)
    )
    for name in fully_missing:
        issue("warning", "FULLY_MISSING_COLUMN", [line for line, _ in records], name)
    if dates != sorted(dates):
        issue("warning", "UNSORTED_DATES", [], "Sort chronologically before temporal splitting")
    issue(
        "warning",
        "UNCONFIRMED_SOURCE_UNITS",
        [],
        "Numeric ranges and relationships do not prove currency, loaded-mile semantics, "
        "rate provenance, or shipment timezone; confirm with the data producer",
    )
    invalid_lines = set(malformed)
    for lines in violations.values():
        invalid_lines.update(lines)
    invalid_lines.update(line for lines in duplicate_ids.values() for line in lines)
    invalid_lines.update(duplicate_lines)
    row_errors = {line: [] for line in sorted(invalid_lines)}
    for line in malformed:
        row_errors[line].append("MALFORMED_ROW")
    for code, lines in violations.items():
        for line in lines:
            row_errors[line].append(code)
    for lines in duplicate_ids.values():
        for line in lines:
            row_errors[line].append("DUPLICATE_SHIPMENT_ID")
    for line in duplicate_lines:
        row_errors[line].append("DUPLICATE_RECORD")
    return {
        "source": str(path),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "as_of_date": as_of.isoformat(),
        "status": "fail"
        if any(item["severity"] == "error" for item in issues)
        else "pass_with_warnings",
        "row_count": len(raw_rows),
        "parsed_row_count": len(records),
        "column_count": len(header),
        "required_columns_missing": missing_columns,
        "unexpected_columns": sorted(set(header) - set(SCHEMA)),
        "units_contract": SCHEMA,
        "order_type_counts": dict(order_counts),
        "positive_open_bid_target_rows": eligible,
        "invalid_row_count": len(invalid_lines),
        "invalid_records": [
            {"csv_line": line, "reasons": sorted(reasons)} for line, reasons in row_errors.items()
        ],
        "duplicate_id_count": len(duplicate_ids),
        "duplicate_record_count": len(duplicate_lines),
        "missing_values": {name: missing_counts[name] for name in header if name in SCHEMA},
        "fully_missing_columns": fully_missing,
        "numeric_ranges": {
            name: {"min": min(values), "max": max(values)}
            for name, values in sorted(numeric_values.items())
            if values
        },
        "date_range": {
            "min": min(dates).isoformat(sep=" ") if dates else None,
            "max": max(dates).isoformat(sep=" ") if dates else None,
            "chronologically_sorted": dates == sorted(dates),
        },
        "unit_consistency_observations": {
            "estimated_cost_matches_miles_times_cpm_within_0_01": cost_matches,
            "market_rpm_equals_all_in_rpm": market_matches,
            "offered_rate_per_mile_matches_market_rpm_within_0_001": reference_matches,
        },
        "issues": issues,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=PROJECT_ROOT / "data/historical_shipments.csv")
    parser.add_argument(
        "--output", type=Path, default=PROJECT_ROOT / "artifacts/data-validation.json"
    )
    parser.add_argument("--as-of", type=date.fromisoformat, default=date.today())
    parser.add_argument("--expected-rows", type=int)
    args = parser.parse_args()
    try:
        report = validate(args.data, args.as_of, args.expected_rows)
    except (OSError, UnicodeError, csv.Error) as error:
        parser.exit(2, f"Unable to read CSV: {error}\n")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(
        f"{report['status']}: {report['row_count']} rows, "
        f"{report['invalid_row_count']} invalid rows; report: {args.output}"
    )
    for entry in report["issues"]:
        print(f"{entry['severity']}: {entry['code']} ({entry['count']} rows): {entry['detail']}")
    return int(report["status"] == "fail")


if __name__ == "__main__":
    raise SystemExit(main())
