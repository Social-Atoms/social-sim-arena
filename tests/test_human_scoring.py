"""Human Wisdom uses point-answer losses on boards separate from agents."""
import math
import unittest

from ssa import human_scoring


class HumanScoring(unittest.TestCase):
    def test_continuous_answers_use_absolute_error(self):
        self.assertEqual(2.5, human_scoring.absolute_error(10, 7.5))

    def test_binary_answers_are_choices_not_probabilities(self):
        self.assertEqual(0.0, human_scoring.zero_one_loss("Yes", "yes"))
        self.assertEqual(1.0, human_scoring.zero_one_loss("No", "Yes"))

    def test_profile_uses_one_point_per_declared_cell(self):
        got = human_scoring.profile_rmse(
            {"group_a": 10, "group_b": 14},
            {"group_a": 13, "group_b": 10},
        )
        self.assertEqual(math.sqrt(12.5), got)
        with self.assertRaises(ValueError):
            human_scoring.profile_rmse({"group_a": 10}, {"group_b": 10})

    def test_fixed_and_open_rankings_reuse_existing_losses(self):
        truth = ["A", "B", "C"]
        self.assertEqual(0.0, human_scoring.ranking_loss(truth, truth))
        self.assertEqual(1.0, human_scoring.ranking_loss(list(reversed(truth)), truth))
        self.assertEqual(0.0, human_scoring.ranking_loss(truth, truth, kind="open"))

    def test_skill_is_relative_to_the_same_board_baseline(self):
        self.assertEqual(0.5, human_scoring.skill(2, 4))
        self.assertEqual(0.0, human_scoring.skill(0, 0))


if __name__ == "__main__":
    unittest.main()
