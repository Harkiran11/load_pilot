"""Positive scalar output, small architecture, gradients, and weight round trips."""

import io
import json
import unittest

import torch

from loadpilot_model.models.regressor import FreightRPMRegressor, RegressorConfig


class RegressorTests(unittest.TestCase):
    def setUp(self):
        self.config = RegressorConfig(input_features=114)
        self.model = FreightRPMRegressor(self.config)

    def test_batch_shapes_positive_outputs_and_float32(self):
        for size in (0, 1, 8):
            with self.subTest(size=size):
                predictions = self.model(torch.zeros(size, 114))
                self.assertEqual(predictions.shape, (size,))
                self.assertEqual(predictions.dtype, torch.float32)
                self.assertTrue(torch.isfinite(predictions).all())
                self.assertTrue((predictions > 0).all())

    def test_initial_model_stays_small_for_the_saved_dataset(self):
        self.assertLess(sum(parameter.numel() for parameter in self.model.parameters()), 5000)

    def test_backward_pass_connects_all_parameters_with_finite_gradients(self):
        predictions = self.model(torch.ones(4, 114))
        predictions.square().mean().backward()
        for parameter in self.model.parameters():
            self.assertIsNotNone(parameter.grad)
            self.assertTrue(torch.isfinite(parameter.grad).all())
        self.assertTrue(
            any(parameter.grad.abs().sum() > 0 for parameter in self.model.parameters())
        )

    def test_config_and_weights_round_trip_reproduce_identical_predictions(self):
        config = RegressorConfig.from_dict(json.loads(json.dumps(self.config.to_dict())))
        self.assertEqual(config, self.config)
        features = torch.linspace(-1, 1, 228).reshape(2, 114)
        self.model.eval()
        with torch.inference_mode():
            expected = self.model(features)
        buffer = io.BytesIO()
        torch.save(self.model.state_dict(), buffer)
        buffer.seek(0)
        restored = FreightRPMRegressor(config)
        restored.load_state_dict(torch.load(buffer, weights_only=True, map_location="cpu"))
        restored.eval()
        with torch.inference_mode():
            self.assertTrue(torch.equal(restored(features), expected))

    def test_invalid_tensor_shape_dtype_or_values_are_rejected(self):
        for features in (
            torch.zeros(114),
            torch.zeros(2, 113),
            torch.zeros(2, 114, dtype=torch.int64),
            torch.zeros(2, 114, dtype=torch.float64),
            torch.full((1, 114), float("nan")),
            torch.full((1, 114), float("inf")),
        ):
            with (
                self.subTest(shape=features.shape, dtype=features.dtype),
                self.assertRaises(ValueError),
            ):
                self.model(features)
        with self.assertRaises(TypeError):
            self.model([[0] * 114])

    def test_invalid_config_is_rejected(self):
        for config in (
            {"input_features": 0},
            {"input_features": True},
            {"input_features": 114, "hidden_sizes": ()},
            {"input_features": 114, "hidden_sizes": (32, -1)},
            {"input_features": 114, "min_rpm": 0},
            {"input_features": 114, "min_rpm": float("nan")},
            {"input_features": 114, "min_rpm": 1e-60},
            {"input_features": 114, "min_rpm": 1e100},
        ):
            with self.subTest(config=config), self.assertRaises(ValueError):
                RegressorConfig(**config)

    def test_softplus_keeps_large_negative_logits_positive(self):
        output_layer = self.model.network[-2]
        with torch.no_grad():
            output_layer.weight.zero_()
            output_layer.bias.fill_(-1000)
        self.assertTrue((self.model(torch.zeros(1, 114)) > 0).all())


if __name__ == "__main__":
    unittest.main()
