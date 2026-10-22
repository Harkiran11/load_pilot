"""Export and load a versioned, training-fitted preprocessing bundle."""

import argparse
import hashlib
import json
import pickle
import platform
from pathlib import Path

import numpy as np
import pandas as pd
import sklearn

from loadpilot_model.features.preprocessing import transform_records
from loadpilot_model.features.schema import (
    CATEGORICAL_FEATURES,
    EXCLUDED_FIELDS,
    FEATURE_COLUMNS,
    FEATURE_PROVENANCE,
    MISSING_FEATURE_TOKENS,
    NUMERIC_FEATURES,
    TARGET_COLUMN,
)
from loadpilot_model.paths import PROJECT_ROOT

SCHEMA_VERSION = 1
JSON_FILES = (
    "feature-schema.json",
    "categorical-mappings.json",
    "numeric-scaler.json",
    "missing-value-policy.json",
)


def digest(contents: bytes) -> str:
    return hashlib.sha256(contents).hexdigest()


def runtime_versions() -> dict:
    return {
        "python": ".".join(platform.python_version_tuple()[:2]),
        "numpy": np.__version__,
        "pandas": pd.__version__,
        "scikit_learn": sklearn.__version__,
    }


def contract_code_hashes() -> dict:
    return {
        name: digest((PROJECT_ROOT / name).read_bytes())
        for name in ("loadpilot_model/features/schema.py", "loadpilot_model/features/preprocessing.py")
    }


def describe_preprocessor(processor, report: dict) -> dict:
    """Read fitted state without fit/refit or access to held-out rows."""
    if report.get("fit_partition") != "train":
        raise ValueError("Bundle must originate from training-only preprocessing")
    if (
        report.get("feature_columns") != list(FEATURE_COLUMNS)
        and tuple(report.get("feature_columns", ())) != FEATURE_COLUMNS
    ):
        raise ValueError("Dataset feature schema does not match the current contract")
    if (
        report.get("sklearn_version") != sklearn.__version__
        or report.get("numpy_version") != np.__version__
    ):
        raise ValueError("Dataset preprocessing dependency versions do not match")
    scaler = processor.named_transformers_["numeric"]
    encoder = processor.named_transformers_["categorical"]
    if (
        not scaler.with_mean
        or not scaler.with_std
        or encoder.drop is not None
        or encoder.handle_unknown != "ignore"
        or encoder.sparse_output
    ):
        raise ValueError("Unsupported fitted preprocessing policy")
    if int(scaler.n_samples_seen_) != report["fit_row_count"]:
        raise ValueError("Scaler fitted row count differs from training manifest")
    if list(processor.feature_names_in_) != list(FEATURE_COLUMNS):
        raise ValueError("Fitted input column order differs from the feature contract")
    names = processor.get_feature_names_out().tolist()
    if names != report["transformed_feature_names"]:
        raise ValueError("Encoded columns differ from the dataset manifest")
    offset = len(NUMERIC_FEATURES)
    categories = {}
    for name, values in zip(CATEGORICAL_FEATURES, encoder.categories_):
        categories[name] = [
            {
                "value": str(value),
                "category_index": index,
                "encoded_column_index": offset + index,
                "encoded_column_name": names[offset + index],
            }
            for index, value in enumerate(values)
        ]
        offset += len(values)
    if offset != len(names):
        raise ValueError("Unexpected encoded output layout")
    schema = {
        "schema_version": SCHEMA_VERSION,
        "raw_feature_order": list(FEATURE_COLUMNS),
        "features": [
            {
                "name": name,
                "type": "number" if name in NUMERIC_FEATURES else "string",
                "required": True,
                **FEATURE_PROVENANCE[name],
            }
            for name in FEATURE_COLUMNS
        ],
        "normalization": "Trim whitespace; uppercase categorical values; numeric strings accepted",
        "lane_derivation": "When lane_id is absent/blank, join posted origin_terminal_code and "
        "destination_terminal_code with '-' in that order; both codes required",
        "numeric_validation": "Finite; positive miles/weight; latitude +/-90, longitude +/-180",
        "excluded_fields": EXCLUDED_FIELDS,
        "unlisted_fields": "Ignored; never added to the model feature matrix",
        "encoded_feature_order": names,
        "encoded_feature_count": len(names),
        "output_dtype": "float32",
        "target": {
            "name": TARGET_COLUMN,
            "units": "USD per loaded mile",
            "scaled": False,
            "required_at_inference": False,
            "formula": "bid_rate / distance_miles",
        },
        "fit_partition": "train",
        "fit_row_count": report["fit_row_count"],
        "source_sha256": report["source_sha256"],
        "training_partition": report["partitions"]["train"],
    }
    policy = {
        "schema_version": SCHEMA_VERSION,
        "missing_tokens_case_insensitive": MISSING_FEATURE_TOKENS,
        "none_and_absent": "Missing",
        "required_features": "Reject; no imputation or dropped rows",
        "optional_source_fields": "Ignored by projection; blanks retain otherwise valid rows",
        "fully_missing_export_helpers": ["fuel_surcharge_rpm", "booked_lead_hours"],
        "unknown_categories": "All-zero one-hot block for that feature; never refit or add columns",
        "unknown_is_missing": False,
        "target_at_inference": "Not required or read",
    }
    return {
        "feature-schema.json": schema,
        "categorical-mappings.json": {"fit_partition": "train", "mappings": categories},
        "numeric-scaler.json": {
            "fit_partition": "train",
            "feature_order": NUMERIC_FEATURES,
            "mean": scaler.mean_.tolist(),
            "scale": scaler.scale_.tolist(),
            "variance": scaler.var_.tolist(),
            "n_samples_seen": int(scaler.n_samples_seen_),
            "formula": "(value - mean) / scale; constant-feature scale is 1",
        },
        "missing-value-policy.json": policy,
    }


def save_inference_bundle(
    processor, report: dict, output: Path, *, preprocessor_bytes: bytes | None = None
) -> dict:
    documents = describe_preprocessor(processor, report)
    contents = {
        name: (json.dumps(value, indent=2, allow_nan=False) + "\n").encode()
        for name, value in documents.items()
    }
    if preprocessor_bytes is not None:
        restored = describe_preprocessor(pickle.loads(preprocessor_bytes), report)
        if json.dumps(restored, sort_keys=True) != json.dumps(documents, sort_keys=True):
            raise ValueError("Original preprocessing bytes differ from the fitted contract")
    contents["preprocessor.pkl"] = (
        preprocessor_bytes if preprocessor_bytes is not None else pickle.dumps(processor)
    )
    hashes = {name: digest(value) for name, value in contents.items()}
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "bundle_id": digest(json.dumps(hashes, sort_keys=True).encode()),
        "files_sha256": hashes,
        "runtime_versions": runtime_versions(),
        "contract_code_sha256": contract_code_hashes(),
        "purpose": "Preprocessing only; no trained PyTorch pricing weights yet",
    }
    output.mkdir(parents=True, exist_ok=False)
    for name, value in contents.items():
        (output / name).write_bytes(value)
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


class InferencePreprocessor:
    """A loaded inference contract; transform never fits on incoming shipments."""

    def __init__(self, processor, documents, manifest):
        self.processor = processor
        self.schema = documents["feature-schema.json"]
        self.categorical_mappings = documents["categorical-mappings.json"]["mappings"]
        self.missing_value_policy = documents["missing-value-policy.json"]
        self.manifest = manifest

    def transform(self, records: list[dict]) -> np.ndarray:
        return transform_records(self.processor, records)


def load_inference_bundle(path: Path) -> InferencePreprocessor:
    """Load a locally trusted bundle, verifying compatibility before unpickling."""
    manifest = json.loads((path / "manifest.json").read_text())
    if manifest["schema_version"] != SCHEMA_VERSION:
        raise ValueError("Unsupported inference schema version")
    if manifest["runtime_versions"] != runtime_versions():
        raise ValueError(
            "Inference dependency versions differ; use the locked training environment"
        )
    if manifest["contract_code_sha256"] != contract_code_hashes():
        raise ValueError("Feature/transform code differs from the saved inference contract")
    contents = {}
    for name in (*JSON_FILES, "preprocessor.pkl"):
        contents[name] = (path / name).read_bytes()
        if digest(contents[name]) != manifest["files_sha256"][name]:
            raise ValueError(f"Inference artifact checksum mismatch: {name}")
    if manifest["bundle_id"] != digest(
        json.dumps(manifest["files_sha256"], sort_keys=True).encode()
    ):
        raise ValueError("Inference bundle identity mismatch")
    documents = {name: json.loads(contents[name]) for name in JSON_FILES}
    schema = documents["feature-schema.json"]
    if schema["raw_feature_order"] != list(FEATURE_COLUMNS):
        raise ValueError("Saved feature order differs from current model inputs")
    processor = pickle.loads(contents["preprocessor.pkl"])
    if processor.get_feature_names_out().tolist() != schema["encoded_feature_order"]:
        raise ValueError("Preprocessor encoded feature order differs from schema")
    expected = describe_preprocessor(
        processor,
        {
            "fit_partition": schema["fit_partition"],
            "fit_row_count": schema["fit_row_count"],
            "feature_columns": schema["raw_feature_order"],
            "transformed_feature_names": schema["encoded_feature_order"],
            "source_sha256": schema["source_sha256"],
            "partitions": {"train": schema["training_partition"]},
            "sklearn_version": sklearn.__version__,
            "numpy_version": np.__version__,
        },
    )
    if json.dumps(expected, sort_keys=True) != json.dumps(documents, sort_keys=True):
        raise ValueError("Saved schema, mappings, or policies differ from fitted preprocessing")
    return InferencePreprocessor(processor, documents, manifest)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=PROJECT_ROOT / "artifacts/dataset")
    parser.add_argument("--output-dir", type=Path, default=PROJECT_ROOT / "artifacts/inference/v2")
    args = parser.parse_args()
    try:
        report = json.loads((args.dataset / "split-report.json").read_text())
        if (
            report["sklearn_version"] != sklearn.__version__
            or report["numpy_version"] != np.__version__
        ):
            raise ValueError("Dataset dependency versions differ from the current environment")
        contents = (args.dataset / "preprocessor.pkl").read_bytes()
        if digest(contents) != report["preprocessor_sha256"]:
            raise ValueError("Dataset preprocessor checksum mismatch")
        processor = pickle.loads(contents)
        manifest = save_inference_bundle(processor, report, args.output_dir)
    except (OSError, ValueError, KeyError) as error:
        parser.exit(2, f"Unable to export inference bundle: {error}\n")
    print(f"Saved inference bundle {manifest['bundle_id'][:12]} to {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
