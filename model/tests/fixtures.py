"""Small public synthetic dataset fixture for Dataset and training tests."""

import csv
import hashlib
import json
import pickle
from pathlib import Path

import numpy as np

from loadpilot_model.data.splitting import build_partitions
from loadpilot_model.features.schema import FEATURE_COLUMNS, METADATA_COLUMNS, TARGET_COLUMN


def make_saved_dataset(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    rows = [
        {
            "shipment_id": f"S-{i:02}",
            "shipped_at": f"2026-01-{i + 1:02} 00:00:00",
            "distance_miles": str(100 + i),
            "weight_lbs": "10000",
            "origin_latitude": "33",
            "origin_longitude": "-84",
            "destination_latitude": "33",
            "destination_longitude": "-97",
            "origin_state": "GA",
            "destination_state": "TX",
            "lane_id": "ATL-DFW",
            "target_bid_rpm": str(2 + i / 10),
        }
        for i in range(20)
    ]
    splits, processor, report = build_partitions(rows)
    contents = pickle.dumps(processor)
    (root / "preprocessor.pkl").write_bytes(contents)
    report.update(source_sha256="a" * 64, preprocessor_sha256=hashlib.sha256(contents).hexdigest())
    (root / "split-report.json").write_text(json.dumps(report))
    for name, partition in splits.items():
        folder = root / name
        folder.mkdir()
        np.savez_compressed(folder / "arrays.npz", X=partition["X"], y=partition["y"])
        for table, fields in (
            ("features", FEATURE_COLUMNS),
            ("targets", (TARGET_COLUMN,)),
            ("metadata", METADATA_COLUMNS),
        ):
            with (folder / f"{table}.csv").open("w", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=fields)
                writer.writeheader()
                writer.writerows(partition[table])
