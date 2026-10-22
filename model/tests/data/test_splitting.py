"""Check chronological isolation and training-only fitted preprocessing."""

import pickle
import unittest

import numpy as np

from loadpilot_model.data.splitting import build_partitions, transform_records
from loadpilot_model.features.schema import NUMERIC_FEATURES


class SplitTests(unittest.TestCase):
    def rows(self, count=20):
        return [
            {
                "shipment_id": f"S-{i:03}",
                "shipped_at": f"2026-01-{i + 1:02} 12:00:00",
                "distance_miles": str(100 + i),
                "weight_lbs": "10000",
                "origin_latitude": "33",
                "origin_longitude": "-84",
                "destination_latitude": "33",
                "destination_longitude": "-97",
                "lane_id": "ATL-DFW",
                "origin_state": "GA",
                "destination_state": "TX",
                "target_bid_rpm": str(2 + i / 10),
            }
            for i in range(count)
        ]

    def test_chronology_disjointness_and_alignment(self):
        rows = self.rows()
        splits, _, report = build_partitions(list(reversed(rows)))
        self.assertEqual([len(partition["y"]) for partition in splits.values()], [14, 3, 3])
        ids = [row["shipment_id"] for partition in splits.values() for row in partition["metadata"]]
        self.assertEqual(ids, [row["shipment_id"] for row in rows])
        self.assertEqual(len(set(ids)), len(rows))
        for partition in splits.values():
            for target, meta, raw_y in zip(
                partition["targets"], partition["metadata"], partition["y"]
            ):
                i = int(meta["shipment_id"].split("-")[1])
                self.assertEqual(float(target["target_bid_rpm"]), 2 + i / 10)
                self.assertAlmostEqual(float(raw_y), 2 + i / 10, places=6)
        self.assertLess(
            report["partitions"]["train"]["date_max"],
            report["partitions"]["validation"]["date_min"],
        )
        self.assertLess(
            report["partitions"]["validation"]["date_max"], report["partitions"]["test"]["date_min"]
        )

    def test_same_timestamp_is_never_split(self):
        rows = self.rows()
        for i in range(12, 16):
            rows[i]["shipped_at"] = "2026-01-14 12:00:00"
        splits, _, _ = build_partitions(rows)
        owners = {}
        for name, partition in splits.items():
            for row in partition["metadata"]:
                self.assertEqual(owners.setdefault(row["shipped_at"], name), name)

    def test_heldout_values_and_labels_never_change_fitted_state(self):
        rows = self.rows()
        _, original, _ = build_partitions(rows)
        for row in rows[14:]:
            row.update(
                distance_miles="999999",
                lane_id="FUTURE-LANE",
                origin_state="ZZ",
                target_bid_rpm="100000",
            )
        splits, processor, report = build_partitions(rows)
        scaler = processor.named_transformers_["numeric"]
        np.testing.assert_array_equal(scaler.mean_, original.named_transformers_["numeric"].mean_)
        np.testing.assert_array_equal(scaler.scale_, original.named_transformers_["numeric"].scale_)
        self.assertEqual(scaler.n_samples_seen_, 14)
        encoder = processor.named_transformers_["categorical"]
        self.assertNotIn("FUTURE-LANE", encoder.categories_[0])
        self.assertEqual(report["fit_row_count"], 14)
        self.assertTrue(np.isfinite(splits["test"]["X"]).all())
        self.assertEqual(float(splits["test"]["X"][0, len(NUMERIC_FEATURES)]), 0)

    def test_serialized_processor_reproduces_validation_transform(self):
        splits, processor, _ = build_partitions(self.rows())
        loaded = pickle.loads(pickle.dumps(processor))
        np.testing.assert_array_equal(
            transform_records(loaded, splits["validation"]["features"]), splits["validation"]["X"]
        )

    def test_reject_invalid_fractions_dates_duplicate_ids_or_insufficient_groups(self):
        for train, validation in ((0, 0.15), (0.9, 0.2), (float("nan"), 0.15), (0.7, -0.1)):
            with self.subTest(train=train), self.assertRaises(ValueError):
                build_partitions(self.rows(), train, validation)
        for rows in (
            self.rows(2),
            [self.rows()[0]] * 3,
            [{**row, "shipped_at": "2026-01-01 00:00:00"} for row in self.rows()],
            [{**row, "shipped_at": "bad"} for row in self.rows()],
        ):
            with self.assertRaises(ValueError):
                build_partitions(rows)


if __name__ == "__main__":
    unittest.main()
