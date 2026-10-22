"""Compare the exact allocator with exhaustive small-batch solutions."""

import itertools
import random
import unittest
from decimal import Decimal

from loadpilot_model.decision.batch import select_assignments


class BatchAllocationTests(unittest.TestCase):
    def test_reassigns_instead_of_greedily_taking_largest_profit(self):
        pairs = [("A", "X", Decimal(900)), ("A", "Y", Decimal(800)), ("B", "X", Decimal(700))]
        self.assertEqual(select_assignments(pairs), [1, 2])

    def test_small_random_batches_match_exhaustive_optimum(self):
        rng = random.Random(42)
        for _ in range(30):
            pairs = [
                (shipment, unit, Decimal(rng.randint(-10, 50)) / 100)
                for shipment in ("A", "B", "C")
                for unit in ("X", "Y", "Z")
                if rng.random() < 0.8
            ]
            optimum = Decimal(0)
            for mask in itertools.product((False, True), repeat=len(pairs)):
                chosen = [pair for pair, take in zip(pairs, mask) if take]
                if len({pair[0] for pair in chosen}) == len(chosen) and len(
                    {pair[1] for pair in chosen}
                ) == len(chosen):
                    optimum = max(optimum, sum((pair[2] for pair in chosen), Decimal(0)))
            selected = [pairs[index] for index in select_assignments(pairs)]
            self.assertEqual(sum((pair[2] for pair in selected), Decimal(0)), optimum)
            self.assertEqual(len({pair[0] for pair in selected}), len(selected))
            self.assertEqual(len({pair[1] for pair in selected}), len(selected))

    def test_empty_and_nonpositive_pairs_select_nothing(self):
        self.assertEqual(select_assignments([]), [])
        self.assertEqual(select_assignments([("A", "X", Decimal(0))]), [])
