"""Check global budget configuration without AWS credentials."""

import importlib.util
import json
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "demo_budget", ROOT / "packaging/demo/budget.py"
)
budget = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(budget)


class BudgetTests(unittest.TestCase):
    def test_new_budget_tracks_usage_before_credits(self):
        with patch.object(
            budget, "aws", side_effect=[{}, {}, {"Budget": {"BudgetName": "demo"}}]
        ) as aws:
            budget.configure("account", "demo", "operator@example.com")
        arguments = aws.call_args_list[1].args
        configuration = json.loads(arguments[arguments.index("--budget") + 1])
        notifications = json.loads(
            arguments[arguments.index("--notifications-with-subscribers") + 1]
        )
        self.assertEqual(configuration["BudgetLimit"], {"Amount": "10", "Unit": "USD"})
        self.assertEqual(
            configuration["CostTypes"], {"IncludeCredit": False, "IncludeRefund": False}
        )
        self.assertEqual(
            [row["Notification"]["Threshold"] for row in notifications], [50, 100]
        )
        self.assertTrue(
            all(
                row["Subscribers"]
                == [{"SubscriptionType": "EMAIL", "Address": "operator@example.com"}]
                for row in notifications
            )
        )

    def test_repeat_preserves_other_subscribers(self):
        notifications = [
            {
                "NotificationType": "ACTUAL",
                "ComparisonOperator": "GREATER_THAN",
                "Threshold": threshold,
            }
            for threshold in (50, 100)
        ]
        subscriber = {"SubscriptionType": "EMAIL", "Address": "operator@example.com"}
        other = {"SubscriptionType": "EMAIL", "Address": "other@example.com"}
        replies = [
            {"Budgets": [{"BudgetName": "demo"}]},
            {},
            {"Notifications": notifications},
            {"Subscribers": [subscriber, other]},
            {"Subscribers": [other]},
            {},
            {"Budget": {"BudgetName": "demo"}},
        ]
        with patch.object(budget, "aws", side_effect=replies) as aws:
            budget.configure("account", "demo", subscriber["Address"])
        actions = [call.args[1] for call in aws.call_args_list]
        self.assertEqual(actions.count("create-subscriber"), 1)
        self.assertNotIn("create-notification", actions)
        self.assertFalse(any(action.startswith("delete-") for action in actions))

    def test_missing_notification_is_created(self):
        replies = [
            {"Budgets": [{"BudgetName": "demo"}]},
            {},
            {"Notifications": []},
            {},
            {},
            {"Budget": {}},
        ]
        with patch.object(budget, "aws", side_effect=replies) as aws:
            budget.configure("account", "demo", "operator@example.com")
        self.assertEqual(
            sum(call.args[1] == "create-notification" for call in aws.call_args_list), 2
        )

    def test_removal_ignores_absent_budget(self):
        with patch.object(budget, "aws", return_value={}) as aws:
            budget.remove("account", "demo")
        self.assertEqual(aws.call_count, 1)
        with patch.object(
            budget, "aws", side_effect=[{"Budgets": [{"BudgetName": "demo"}]}, {}]
        ) as aws:
            budget.remove("account", "demo")
        self.assertEqual(
            aws.call_args.args,
            (
                "budgets",
                "delete-budget",
                "--account-id",
                "account",
                "--budget-name",
                "demo",
            ),
        )

    def test_empty_cli_response_is_valid(self):
        with patch.object(
            budget.subprocess, "check_output", return_value=""
        ) as command:
            self.assertEqual(budget.aws("budgets", "describe-budgets"), {})
        self.assertEqual(command.call_args.args[0][1:3], ["--region", "us-east-1"])


if __name__ == "__main__":
    unittest.main()
