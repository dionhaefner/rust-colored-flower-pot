"""Manage account-wide demo alerts through the global AWS Budgets API."""

import json
import subprocess


def aws(*arguments):
    output = subprocess.check_output(
        [
            "aws",
            "--region",
            "us-east-1",
            "--no-cli-pager",
            "--output",
            "json",
            *arguments,
        ],
        text=True,
    )
    return json.loads(output) if output.strip() else {}


def exists(account, name):
    result = aws("budgets", "describe-budgets", "--account-id", account)
    return any(row["BudgetName"] == name for row in result.get("Budgets", []))


def configure(account, name, email):
    budget = {
        "BudgetName": name,
        "BudgetType": "COST",
        "TimeUnit": "MONTHLY",
        "BudgetLimit": {"Amount": "10", "Unit": "USD"},
        "CostTypes": {"IncludeCredit": False, "IncludeRefund": False},
    }
    notifications = [
        {
            "Notification": {
                "NotificationType": "ACTUAL",
                "ComparisonOperator": "GREATER_THAN",
                "Threshold": threshold,
                "ThresholdType": "PERCENTAGE",
            },
            "Subscribers": [{"SubscriptionType": "EMAIL", "Address": email}],
        }
        for threshold in (50, 100)
    ]
    if not exists(account, name):
        aws(
            "budgets",
            "create-budget",
            "--account-id",
            account,
            "--budget",
            json.dumps(budget),
            "--notifications-with-subscribers",
            json.dumps(notifications),
        )
    else:
        aws(
            "budgets",
            "update-budget",
            "--account-id",
            account,
            "--new-budget",
            json.dumps(budget),
        )
        existing = aws(
            "budgets",
            "describe-notifications-for-budget",
            "--account-id",
            account,
            "--budget-name",
            name,
        ).get("Notifications", [])
        for row in notifications:
            notification, subscribers = row["Notification"], row["Subscribers"]
            common = [
                "--account-id",
                account,
                "--budget-name",
                name,
                "--notification",
                json.dumps(notification),
            ]
            if any(
                all(
                    current.get(key, "PERCENTAGE" if key == "ThresholdType" else None)
                    == value
                    for key, value in notification.items()
                )
                for current in existing
            ):
                current = aws(
                    "budgets",
                    "describe-subscribers-for-notification",
                    *common,
                ).get("Subscribers", [])
                if subscribers[0] not in current:
                    aws(
                        "budgets",
                        "create-subscriber",
                        *common,
                        "--subscriber",
                        json.dumps(subscribers[0]),
                    )
            else:
                aws(
                    "budgets",
                    "create-notification",
                    *common,
                    "--subscribers",
                    json.dumps(subscribers),
                )
    return aws(
        "budgets",
        "describe-budget",
        "--account-id",
        account,
        "--budget-name",
        name,
    )["Budget"]


def remove(account, name):
    if exists(account, name):
        aws("budgets", "delete-budget", "--account-id", account, "--budget-name", name)
