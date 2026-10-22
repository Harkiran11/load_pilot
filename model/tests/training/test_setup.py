"""Regression objective arithmetic, optimizer configuration, and reproducible steps."""

import json
import os
import random
import unittest

import numpy as np
import torch

from loadpilot_model.models.regressor import RegressorConfig
from loadpilot_model.training.setup import (
    RPMHuberLoss,
    TrainingSetupConfig,
    build_training_components,
    set_random_seeds,
)


class TrainingSetupTests(unittest.TestCase):
    def setUp(self):
        self.python_state = random.getstate()
        self.numpy_state = np.random.get_state()
        self.torch_state = torch.get_rng_state()
        self.deterministic = torch.are_deterministic_algorithms_enabled()
        self.warn_only = torch.is_deterministic_algorithms_warn_only_enabled()
        self.cudnn = (torch.backends.cudnn.deterministic, torch.backends.cudnn.benchmark)
        self.workspace = os.environ.get("CUBLAS_WORKSPACE_CONFIG")
        self.addCleanup(self.restore_rng)

    def restore_rng(self):
        random.setstate(self.python_state)
        np.random.set_state(self.numpy_state)
        torch.set_rng_state(self.torch_state)
        torch.use_deterministic_algorithms(self.deterministic, warn_only=self.warn_only)
        torch.backends.cudnn.deterministic, torch.backends.cudnn.benchmark = self.cudnn
        if self.workspace is None:
            os.environ.pop("CUBLAS_WORKSPACE_CONFIG", None)
        else:
            os.environ["CUBLAS_WORKSPACE_CONFIG"] = self.workspace

    def test_huber_loss_has_expected_quadratic_and_linear_behavior(self):
        predictions = torch.tensor([1.25, 3.0], requires_grad=True)
        loss = RPMHuberLoss(delta=0.5)(predictions, torch.tensor([1.0, 1.0]))
        self.assertAlmostEqual(loss.item(), 0.453125)
        loss.backward()
        torch.testing.assert_close(predictions.grad, torch.tensor([0.125, 0.25]))

    def test_shape_broadcasting_invalid_values_and_empty_batches_rejected(self):
        criterion = RPMHuberLoss()
        for predictions, targets in (
            (torch.ones(2, 1), torch.ones(2)),
            (torch.ones(2), torch.ones(3)),
            (torch.empty(0), torch.empty(0)),
            (torch.ones(2), torch.zeros(2)),
            (torch.ones(2), torch.tensor([1.0, float("nan")])),
            (torch.ones(2, dtype=torch.float64), torch.ones(2)),
        ):
            with self.subTest(shape=predictions.shape), self.assertRaises(ValueError):
                criterion(predictions, targets)

    def test_adamw_uses_validated_settings_and_same_model_parameters(self):
        config = TrainingSetupConfig(learning_rate=0.001, weight_decay=0.02, huber_delta=0.75)
        components = build_training_components(RegressorConfig(4), config)
        self.assertIsInstance(components.optimizer, torch.optim.AdamW)
        self.assertEqual(components.optimizer.param_groups[0]["lr"], 0.001)
        self.assertEqual(components.optimizer.param_groups[0]["weight_decay"], 0.02)
        self.assertEqual(components.criterion.delta, 0.75)
        self.assertEqual(
            [id(p) for p in components.model.parameters()],
            [id(p) for p in components.optimizer.param_groups[0]["params"]],
        )
        self.assertFalse(components.optimizer.state)

    def test_same_seed_reproduces_initialization_and_one_synthetic_optimizer_step(self):
        features = torch.linspace(-1, 1, 16).reshape(4, 4)
        targets = torch.tensor([2.0, 2.5, 3.0, 3.5])

        def run(seed):
            components = build_training_components(
                RegressorConfig(4), TrainingSetupConfig(seed=seed)
            )
            before = {
                key: value.detach().clone() for key, value in components.model.state_dict().items()
            }
            loss = components.criterion(components.model(features), targets)
            components.optimizer.zero_grad(set_to_none=True)
            loss.backward()
            components.optimizer.step()
            after = {
                key: value.detach().clone() for key, value in components.model.state_dict().items()
            }
            return before, after

        before, first = run(42)
        _, second = run(42)
        different, _ = run(43)
        self.assertTrue(all(torch.equal(first[key], second[key]) for key in first))
        self.assertTrue(any(not torch.equal(before[key], first[key]) for key in first))
        self.assertTrue(any(not torch.equal(before[key], different[key]) for key in first))

    def test_all_rngs_and_deterministic_flags_are_set(self):
        set_random_seeds(42)
        first = random.random(), float(np.random.random()), float(torch.rand(()))
        set_random_seeds(42)
        second = random.random(), float(np.random.random()), float(torch.rand(()))
        self.assertEqual(first, second)
        self.assertTrue(torch.are_deterministic_algorithms_enabled())
        self.assertTrue(torch.backends.cudnn.deterministic)
        self.assertFalse(torch.backends.cudnn.benchmark)

    def test_invalid_configs_rejected_and_json_round_trip(self):
        for values in (
            {"seed": -1},
            {"seed": 2**32},
            {"seed": True},
            {"learning_rate": 0},
            {"learning_rate": float("inf")},
            {"weight_decay": -1},
            {"huber_delta": 0},
            {"huber_delta": float("nan")},
            {"deterministic": "yes"},
        ):
            with self.subTest(values=values), self.assertRaises(ValueError):
                TrainingSetupConfig(**values)
        config = TrainingSetupConfig()
        self.assertEqual(TrainingSetupConfig(**json.loads(json.dumps(config.to_dict()))), config)


if __name__ == "__main__":
    unittest.main()
