from unittest import TestCase

from app.services.reaction_service import _reaction_notification_milestone


class ReactionServiceTests(TestCase):
    def test_skips_notification_before_first_ten_reactions(self) -> None:
        milestone = _reaction_notification_milestone(
            total_reactions=9,
            reaction_total_delta=1,
            last_notified_milestone=0,
        )

        self.assertIsNone(milestone)

    def test_notifies_on_first_ten_reaction_milestone(self) -> None:
        milestone = _reaction_notification_milestone(
            total_reactions=10,
            reaction_total_delta=1,
            last_notified_milestone=0,
        )

        self.assertEqual(milestone, 1)

    def test_skips_notification_when_latest_milestone_was_already_sent(self) -> None:
        milestone = _reaction_notification_milestone(
            total_reactions=10,
            reaction_total_delta=1,
            last_notified_milestone=1,
        )

        self.assertIsNone(milestone)

    def test_skips_notification_when_total_did_not_grow(self) -> None:
        milestone = _reaction_notification_milestone(
            total_reactions=10,
            reaction_total_delta=0,
            last_notified_milestone=0,
        )

        self.assertIsNone(milestone)

    def test_notifies_again_on_next_ten_reaction_milestone(self) -> None:
        milestone = _reaction_notification_milestone(
            total_reactions=20,
            reaction_total_delta=1,
            last_notified_milestone=1,
        )

        self.assertEqual(milestone, 2)
