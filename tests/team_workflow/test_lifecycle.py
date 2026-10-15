import unittest
from scripts.team_workflow.lifecycle import plan_status


def pr(state="open", draft=False, merged=False, change=False):
    return {"state": state, "draft": draft, "merged": merged, "changes_requested": change}


class LifecycleTests(unittest.TestCase):
    def test_draft_ready_and_requested_changes(self):
        for pull, expected in [(pr(draft=True), "In progress"), (pr(), "In review"), (pr(change=True), "Changes requested")]:
            self.assertEqual(plan_status("Not started", False, [pull]), (expected, []))

    def test_all_implementation_prs_must_merge(self):
        merged = pr("closed", merged=True)
        self.assertEqual(plan_status("In review", False, [merged]), ("Implemented", []))
        self.assertEqual(plan_status("In review", False, [merged, pr(draft=True)]), ("In progress", []))
        self.assertEqual(plan_status("In review", False, [merged, pr()]), ("In review", []))

    def test_manual_states_and_closed_issues_are_preserved(self):
        for current in ["Done", "Blocked"]:
            self.assertEqual(plan_status(current, False, [pr()]), (None, []))
        self.assertEqual(plan_status("In review", True, [pr()]), (None, []))

    def test_closed_unmerged_requires_triage_and_never_completes_task(self):
        status, errors = plan_status("In review", False, [pr("closed"), pr("closed")])
        self.assertIsNone(status)
        self.assertTrue(errors)

    def test_abandoned_pr_does_not_block_its_replacement(self):
        for replacement, expected in [
            (pr(draft=True), "In progress"),
            (pr(), "In review"),
            (pr(change=True), "Changes requested"),
            (pr("closed", merged=True), "Implemented"),
        ]:
            for pulls in [[pr("closed"), replacement], [replacement, pr("closed")]]:
                with self.subTest(expected=expected, pulls=pulls):
                    self.assertEqual(plan_status("In review", False, pulls), (expected, []))

    def test_replacement_preserves_manual_states_and_closed_issue(self):
        pulls = [pr("closed"), pr("closed", merged=True)]
        for current in ["Done", "Blocked"]:
            self.assertEqual(plan_status(current, False, pulls), (None, []))
        self.assertEqual(plan_status("In review", True, pulls), (None, []))

    def test_reopened_pr_returns_to_review_and_no_pr_keeps_status(self):
        self.assertEqual(plan_status("Implemented", False, [pr()]), ("In review", []))
        self.assertEqual(plan_status("In progress", False, []), (None, []))

    def test_changes_requested_takes_priority_over_other_open_prs(self):
        self.assertEqual(plan_status("In review", False, [pr(draft=True), pr(change=True)]), ("Changes requested", []))
