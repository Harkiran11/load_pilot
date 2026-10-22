"""Select validated historical open bids for subsequent pricing-model preparation."""

import argparse
import csv
import hashlib
import io
import json
import math
from collections import Counter
from datetime import date
from pathlib import Path

from loadpilot_model.data.validation import MISSING, OPTIONAL, validate
from loadpilot_model.features.schema import (
    CATEGORICAL_FEATURES,
    EXCLUDED_FIELDS,
    FEATURE_COLUMNS,
    FEATURE_PROVENANCE,
    METADATA_COLUMNS,
    NUMERIC_FEATURES,
    TARGET_COLUMN,
    build_model_inputs,
)
from loadpilot_model.paths import PROJECT_ROOT


def load_open_bid_rows(path: Path, as_of: date) -> tuple[list[dict[str, str]], dict]:
    """Return staging records and an exclusion audit; do not construct model inputs."""
    validation = validate(path, as_of)
    file_errors = [
        issue
        for issue in validation["issues"]
        if issue["severity"] == "error" and issue["code"] in {"INVALID_SCHEMA", "EMPTY_DATASET"}
    ]
    if file_errors:
        raise ValueError("Dataset cannot be selected: " + ", ".join(i["code"] for i in file_errors))

    contents = path.read_bytes()
    if hashlib.sha256(contents).hexdigest() != validation["sha256"]:
        raise ValueError("Source changed after validation; rerun preparation")
    reader = csv.reader(io.StringIO(contents.decode("utf-8-sig"), newline=""), strict=True)
    columns = next(reader)
    invalid = {entry["csv_line"]: entry["reasons"] for entry in validation["invalid_records"]}
    selected = []
    exclusions = []
    order_counts = Counter()
    award_counts = Counter()
    for values in reader:
        line = reader.line_num
        record = dict(zip(columns, values))
        order_type = record.get("order_type", "").strip().lower()
        order_counts[order_type or "<missing>"] += 1
        reasons = list(invalid.get(line, []))
        if order_type == "fixed_rate":
            reasons.append("FIXED_RATE_OUTSIDE_MODEL_SCOPE")
        elif order_type != "open_bid":
            reasons.append("UNSUPPORTED_ORDER_TYPE")
        target = None
        if not reasons:
            target = float(record["bid_rate"]) / float(record["distance_miles"])
            if not math.isfinite(target) or target <= 0:
                reasons.append("INVALID_TARGET_BID_RPM")
        if reasons:
            exclusions.append(
                {
                    "csv_line": line,
                    "shipment_id": record.get("shipment_id"),
                    "order_type": order_type,
                    "reasons": sorted(set(reasons)),
                }
            )
            continue
        clean = {name: value.strip() for name, value in record.items()}
        for name in ("order_type", "won", "season"):
            clean[name] = clean[name].lower()
        # Preserve float precision; round only presentation values, not training labels.
        clean["target_bid_rpm"] = repr(target)
        selected.append(clean)
        award_counts[clean["won"]] += 1

    report = {
        "source": str(path),
        "sha256": validation["sha256"],
        "as_of_date": as_of.isoformat(),
        "status": "selected" if selected else "no_eligible_rows",
        "source_row_count": validation["row_count"],
        "source_columns": columns,
        "output_columns": [name for name in columns if name != "target_bid_rpm"]
        + ["target_bid_rpm"],
        "target": {
            "name": "target_bid_rpm",
            "formula": "bid_rate / distance_miles",
            "units": "USD per loaded mile for the confirmed historical source",
            "count": len(selected),
            "min": min(float(row["target_bid_rpm"]) for row in selected) if selected else None,
            "max": max(float(row["target_bid_rpm"]) for row in selected) if selected else None,
        },
        "order_type_counts": dict(order_counts),
        "selected_row_count": len(selected),
        "excluded_row_count": len(exclusions),
        "selected_award_counts": dict(award_counts),
        "exclusion_reason_counts": dict(
            Counter(reason for entry in exclusions for reason in entry["reasons"])
        ),
        "exclusions": exclusions,
        "validation_issues": validation["issues"],
        "missing_data": {
            "optional_columns_absent": sorted(OPTIONAL - set(columns)),
            "source_fully_missing_columns": validation["fully_missing_columns"],
            "selected_optional_missing_counts": {
                name: sum(row.get(name, "").lower() in MISSING for row in selected)
                for name in sorted(OPTIONAL)
            },
            "fully_missing_columns_excluded_from_features": [
                name for name in validation["fully_missing_columns"] if name not in FEATURE_COLUMNS
            ],
            "policy": "Retain rows with empty/absent optional fields; do not impute them as zero. "
            "Required feature or target violations still exclude records. "
            "The fixed feature allowlist omits optional and fully missing export helpers.",
        },
        "purpose": "Validated open-bid staging records, not a leakage-safe feature matrix",
    }
    return selected, report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=PROJECT_ROOT / "data/historical_shipments.csv")
    parser.add_argument("--output-dir", type=Path, default=PROJECT_ROOT / "artifacts/prepared")
    parser.add_argument("--as-of", type=date.fromisoformat, default=date.today())
    args = parser.parse_args()
    try:
        rows, report = load_open_bid_rows(args.data, args.as_of)
        features, targets, metadata = build_model_inputs(rows)
    except (ValueError, OSError, UnicodeError, csv.Error) as error:
        parser.exit(2, f"Unable to prepare open bids: {error}\n")
    output = args.output_dir / "open_bid_shipments.csv"
    audit = args.output_dir / "selection-report.json"
    features_path = args.output_dir / "model_features.csv"
    targets_path = args.output_dir / "model_targets.csv"
    metadata_path = args.output_dir / "model_metadata.csv"
    if args.data.resolve() in {
        path.resolve() for path in (output, audit, features_path, targets_path, metadata_path)
    }:
        parser.exit(2, "Output paths must not overwrite the source data\n")
    report["model_inputs"] = {
        "feature_columns": FEATURE_COLUMNS,
        "numeric_features": NUMERIC_FEATURES,
        "categorical_features": CATEGORICAL_FEATURES,
        "feature_provenance": FEATURE_PROVENANCE,
        "availability_rule": "Posted/planned values known at bidding; no post-shipment corrections",
        "target_column": TARGET_COLUMN,
        "metadata_columns": METADATA_COLUMNS,
        "excluded_source_columns": {
            name: EXCLUDED_FIELDS.get(name, "Not on the approved pre-bid feature allowlist")
            for name in report["output_columns"]
            if name not in FEATURE_COLUMNS
        },
        "row_count": len(features),
        "alignment": "Identical row order across features, targets, metadata, and staging CSV",
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for path, fields, records in (
        (output, report["output_columns"], rows),
        (features_path, FEATURE_COLUMNS, features),
        (targets_path, (TARGET_COLUMN,), targets),
        (metadata_path, METADATA_COLUMNS, metadata),
    ):
        with path.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            writer.writerows(records)
    audit.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(f"Selected {len(rows)} open bids; excluded {report['excluded_row_count']} records.")
    print(f"Staging CSV: {output}\nSelection report: {audit}")
    print(f"Model inputs: {features_path}\nTargets: {targets_path}\nMetadata: {metadata_path}")
    return int(not rows)


if __name__ == "__main__":
    raise SystemExit(main())
