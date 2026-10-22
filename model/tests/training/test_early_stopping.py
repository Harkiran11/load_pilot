"""Patience, significant improvement, and exact minimum checkpoint semantics."""

import unittest

from loadpilot_model.training.early_stopping import EarlyStopping


class EarlyStoppingTests(unittest.TestCase):
    def test_plateau_stops_after_patience_and_rejects_further_updates(self):
        stop = EarlyStopping(patience=2, min_delta=0)
        self.assertTrue(stop.update(1, 0.5))
        self.assertFalse(stop.update(2, 0.5))
        self.assertFalse(stop.should_stop)
        self.assertFalse(stop.update(3, 0.6))
        self.assertTrue(stop.should_stop)
        self.assertEqual(stop.best_epoch, 1)
        with self.assertRaises(ValueError):
            stop.update(4, 0.1)

    def test_small_minima_save_and_cumulative_improvement_resets_patience(self):
        stop = EarlyStopping(patience=3, min_delta=0.1)
        for epoch, loss in enumerate([1.0, 0.96, 0.92], 1):
            self.assertTrue(stop.update(epoch, loss))
        self.assertEqual(stop.bad_epochs, 2)
        self.assertTrue(stop.update(4, 0.89))
        self.assertEqual(stop.bad_epochs, 0)
        self.assertEqual(stop.best_epoch, 4)
        self.assertEqual(stop.best_loss, 0.89)

    def test_invalid_configuration_losses_and_epoch_order_are_rejected(self):
        for patience in (0, -1, True, 1.5):
            with self.assertRaises(ValueError):
                EarlyStopping(patience=patience)
        for delta in (-1, float("nan"), float("inf"), True):
            with self.assertRaises(ValueError):
                EarlyStopping(min_delta=delta)
        stop = EarlyStopping()
        for loss in (-1, float("nan"), float("inf"), True):
            with self.assertRaises(ValueError):
                stop.update(1, loss)
        with self.assertRaises(ValueError):
            stop.update(2, 0.5)
        self.assertEqual(stop.last_epoch, 0)
