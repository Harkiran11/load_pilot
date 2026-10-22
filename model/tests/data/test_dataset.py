"""Tensor shapes, saved-row integrity, reproducible batches, and test-access boundaries."""

import tempfile
import unittest
from pathlib import Path

import numpy as np
import torch

from loadpilot_model.data.dataset import (
    FreightDataset,
    create_dataloader,
    create_train_validation_loaders,
)
from tests.fixtures import make_saved_dataset


class FreightDatasetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        make_saved_dataset(self.root)

    def test_dataset_returns_feature_tensor_and_scalar_rpm_without_metadata(self):
        dataset = FreightDataset(self.root)
        x, y = dataset[0]
        self.assertEqual(len(dataset), 14)
        self.assertEqual(x.shape, (len(dataset.feature_names),))
        self.assertEqual(y.shape, ())
        self.assertEqual(x.dtype, torch.float32)
        self.assertEqual(y.dtype, torch.float32)
        self.assertEqual(dataset.metadata[0].shipment_id, "S-00")

    def test_seeded_training_shuffle_and_validation_order_keep_all_records(self):
        train, validation = create_train_validation_loaders(self.root, batch_size=5)
        x, y = next(iter(train))
        self.assertEqual(x.shape, (5, train.dataset.features.shape[1]))
        self.assertEqual(y.shape, (5,))
        dataset = FreightDataset(self.root)

        def order(seed):
            return torch.cat([y for _, y in create_dataloader(dataset, batch_size=5, seed=seed)])

        first = order(42)
        self.assertTrue(torch.equal(first, order(42)))
        self.assertFalse(torch.equal(first, order(43)))
        self.assertTrue(torch.equal(first.sort().values, dataset.targets.sort().values))
        self.assertEqual([len(y) for _, y in create_dataloader(dataset, batch_size=5)], [5, 5, 4])
        self.assertTrue(
            torch.equal(torch.cat([y for _, y in validation]), validation.dataset.targets)
        )

    def test_test_access_requires_explicit_opt_in(self):
        with self.assertRaisesRegex(ValueError, "reserved"):
            FreightDataset(self.root, "test")
        dataset = FreightDataset(self.root, "test", allow_test=True)
        self.assertTrue(
            torch.equal(torch.cat([y for _, y in create_dataloader(dataset)]), dataset.targets)
        )

    def test_train_validation_do_not_open_test_files(self):
        (self.root / "test/arrays.npz").write_bytes(b"unreadable reserved test")
        train, validation = create_train_validation_loaders(self.root)
        self.assertEqual(len(train.dataset), 14)
        self.assertEqual(len(validation.dataset), 3)

    def test_nonfinite_nonpositive_wrong_dtype_or_shape_rejected(self):
        path = self.root / "train/arrays.npz"
        with np.load(path, allow_pickle=False) as archive:
            original_x, original_y = archive["X"], archive["y"]
        for case in ("nan", "zero", "dtype", "shape"):
            x, y = original_x.copy(), original_y.copy()
            if case == "nan":
                x[0, 0] = np.nan
            if case == "zero":
                y[0] = 0
            if case == "dtype":
                x = x.astype(np.float64)
            if case == "shape":
                y = y.reshape(-1, 1)
            np.savez_compressed(path, X=x, y=y)
            with self.subTest(case=case), self.assertRaises(ValueError):
                FreightDataset(self.root)

    def test_swapped_feature_rows_or_targets_are_rejected(self):
        path = self.root / "train/arrays.npz"
        with np.load(path, allow_pickle=False) as archive:
            x, y = archive["X"], archive["y"]
        changed = x.copy()
        changed[[0, 1]] = changed[[1, 0]]
        np.savez_compressed(path, X=changed, y=y)
        with self.assertRaisesRegex(ValueError, "Feature array"):
            FreightDataset(self.root)
        changed_y = y.copy()
        changed_y[[0, 1]] = changed_y[[1, 0]]
        np.savez_compressed(path, X=x, y=changed_y)
        with self.assertRaisesRegex(ValueError, "Target array"):
            FreightDataset(self.root)

    def test_invalid_loader_options_are_rejected(self):
        dataset = FreightDataset(self.root)
        for options in ({"batch_size": 0}, {"batch_size": True}, {"num_workers": -1}, {"seed": -1}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                create_dataloader(dataset, **options)


if __name__ == "__main__":
    unittest.main()
