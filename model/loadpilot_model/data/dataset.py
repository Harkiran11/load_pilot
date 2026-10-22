"""Validated PyTorch datasets/loaders for saved chronological freight partitions."""

import csv
import hashlib
import json
import pickle
import random
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np
import sklearn
import torch
from torch.utils.data import DataLoader, Dataset

from loadpilot_model.features.preprocessing import transform_records
from loadpilot_model.features.schema import FEATURE_COLUMNS, METADATA_COLUMNS, TARGET_COLUMN


@dataclass(frozen=True)
class ShipmentMetadata:
    shipment_id: str
    shipped_at: str


def _read_csv(path: Path, fields) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream, strict=True)
        if reader.fieldnames != list(fields):
            raise ValueError(f"Unexpected columns in {path}")
        rows = list(reader)
    if any(None in row or any(value is None for value in row.values()) for row in rows):
        raise ValueError(f"Malformed CSV rows in {path}")
    return rows


class FreightDataset(Dataset):
    """Return (encoded float32 features, scalar RPM target); metadata stays separate."""

    def __init__(self, dataset_dir: Path, partition: str = "train", *, allow_test: bool = False):
        if partition not in {"train", "validation", "test"}:
            raise ValueError("Partition must be train, validation, or test")
        if partition == "test" and not allow_test:
            raise ValueError(
                "Test is reserved; explicit allow_test=True is required for final evaluation"
            )
        root = Path(dataset_dir)
        manifest_bytes = (root / "split-report.json").read_bytes()
        report = json.loads(manifest_bytes)
        if (
            report["fit_partition"] != "train"
            or report["target_column"] != TARGET_COLUMN
            or report["feature_columns"] != list(FEATURE_COLUMNS)
        ):
            raise ValueError("Dataset manifest does not match the training-only freight contract")
        if (
            report["sklearn_version"] != sklearn.__version__
            or report["numpy_version"] != np.__version__
        ):
            raise ValueError("Use the locked preprocessing environment for this dataset")
        saved = report["partitions"][partition]
        self.partition = partition
        self.feature_names = tuple(report["transformed_feature_names"])
        if not self.feature_names or len(set(self.feature_names)) != len(self.feature_names):
            raise ValueError("Encoded feature names must be nonempty and unique")
        folder = root / partition
        array_path = folder / "arrays.npz"
        with np.load(array_path, allow_pickle=False) as arrays:
            if set(arrays.files) != {"X", "y"}:
                raise ValueError("Expected exactly X and y in partition archive")
            x, y = arrays["X"], arrays["y"]
        if (
            x.dtype != np.float32
            or y.dtype != np.float32
            or x.ndim != 2
            or y.ndim != 1
            or x.shape != (saved["row_count"], len(self.feature_names))
            or len(y) != len(x)
            or len(y) < 1
        ):
            raise ValueError(
                "X/y must be aligned nonempty float32 arrays with the saved feature width"
            )
        if not np.isfinite(x).all() or not np.isfinite(y).all() or not (y > 0).all():
            raise ValueError("Dataset features must be finite and RPM targets finite and positive")
        features = _read_csv(folder / "features.csv", FEATURE_COLUMNS)
        targets = _read_csv(folder / "targets.csv", (TARGET_COLUMN,))
        metadata = _read_csv(folder / "metadata.csv", METADATA_COLUMNS)
        if any(len(rows) != len(y) for rows in (features, targets, metadata)):
            raise ValueError("Raw features/targets/metadata must align with the array rows")
        ids = [row["shipment_id"] for row in metadata]
        if not all(ids) or len(set(ids)) != len(ids) or ids != saved["shipment_ids"]:
            raise ValueError("Dataset IDs do not match the ordered partition manifest")
        stamps = [datetime.strptime(row["shipped_at"], "%Y-%m-%d %H:%M:%S") for row in metadata]
        if (
            stamps != sorted(stamps)
            or stamps[0].isoformat(sep=" ") != saved["date_min"]
            or stamps[-1].isoformat(sep=" ") != saved["date_max"]
        ):
            raise ValueError("Dataset timestamps do not match the chronological partition")
        expected_y = np.array([float(row[TARGET_COLUMN]) for row in targets], dtype=np.float32)
        if not np.array_equal(y, expected_y):
            raise ValueError("Target array order/values differ from the saved target CSV")
        # Read locally produced preprocessing; verify it, then transform only (never fit).
        contents = (root / "preprocessor.pkl").read_bytes()
        if hashlib.sha256(contents).hexdigest() != report["preprocessor_sha256"]:
            raise ValueError("Dataset preprocessor checksum mismatch")
        processor = pickle.loads(contents)
        if processor.get_feature_names_out().tolist() != list(self.feature_names):
            raise ValueError("Dataset encoded feature order differs from the fitted processor")
        if not np.array_equal(x, transform_records(processor, features)):
            raise ValueError(
                "Feature array order/values differ from the saved feature CSV transform"
            )
        self.features = torch.from_numpy(np.ascontiguousarray(x).copy())
        self.targets = torch.from_numpy(np.ascontiguousarray(y).copy())
        self.metadata = tuple(ShipmentMetadata(**row) for row in metadata)
        self.source_sha256 = report["source_sha256"]
        self.split_manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
        self.preprocessor_sha256 = report["preprocessor_sha256"]
        self.loaded_miles = np.array(
            [float(row["distance_miles"]) for row in features], dtype=np.float64
        )
        self.target_rpm_exact = np.array(
            [float(row[TARGET_COLUMN]) for row in targets], dtype=np.float64
        )
        self.array_sha256 = hashlib.sha256(array_path.read_bytes()).hexdigest()

    def __len__(self) -> int:
        return len(self.targets)

    def __getitem__(self, index: int) -> tuple[torch.Tensor, torch.Tensor]:
        return self.features[index], self.targets[index]


def _seed_worker(_worker_id: int) -> None:
    seed = torch.initial_seed() % 2**32
    random.seed(seed)
    np.random.seed(seed)


def create_dataloader(
    dataset: FreightDataset, *, batch_size: int = 64, seed: int = 42, num_workers: int = 0
) -> DataLoader:
    if isinstance(batch_size, bool) or not isinstance(batch_size, int) or batch_size < 1:
        raise ValueError("batch_size must be a positive integer")
    if isinstance(num_workers, bool) or not isinstance(num_workers, int) or num_workers < 0:
        raise ValueError("num_workers must be a nonnegative integer")
    if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed < 2**63:
        raise ValueError("seed must be an integer in [0, 2**63)")
    generator = torch.Generator().manual_seed(seed)
    return DataLoader(
        dataset,
        batch_size=batch_size,
        shuffle=dataset.partition == "train",
        drop_last=False,
        num_workers=num_workers,
        generator=generator,
        worker_init_fn=_seed_worker,
    )


def create_train_validation_loaders(
    dataset_dir: Path, *, batch_size: int = 64, seed: int = 42, num_workers: int = 0
) -> tuple[DataLoader, DataLoader]:
    """Prepare training/validation only; no implicit test-directory access."""
    options = {"batch_size": batch_size, "seed": seed, "num_workers": num_workers}
    return (
        create_dataloader(FreightDataset(dataset_dir, "train"), **options),
        create_dataloader(FreightDataset(dataset_dir, "validation"), **options),
    )
