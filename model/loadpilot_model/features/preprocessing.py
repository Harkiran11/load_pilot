"""Shared transform-only helpers for historical partitions and inference."""

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer

from loadpilot_model.features.schema import FEATURE_COLUMNS, NUMERIC_FEATURES, build_features


def feature_frame(features: list[dict]) -> pd.DataFrame:
    frame = pd.DataFrame(features, columns=FEATURE_COLUMNS)
    frame[list(NUMERIC_FEATURES)] = frame[list(NUMERIC_FEATURES)].astype(float)
    return frame


def transform_records(processor: ColumnTransformer, records: list[dict]) -> np.ndarray:
    """Apply saved preprocessing to new records without learning from them."""
    if not records:
        return np.empty((0, len(processor.get_feature_names_out())), dtype=np.float32)
    features = [build_features(record) for record in records]
    result = processor.transform(feature_frame(features)).astype(np.float32)
    if not np.isfinite(result).all():
        raise ValueError("Transformed features contain nonfinite values")
    return result
