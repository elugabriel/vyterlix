"""Permission codes and the Manager "remit".

Codes must match the rows seeded in the `permissions` table (migration 538284f717b1);
tests/test_permissions.py fails if they drift apart. Which role holds which permission
lives in the database (`role_permissions`), not here.
"""

from enum import StrEnum


class Perm(StrEnum):
    ORG_MANAGE = "org.manage"
    BILLING_MANAGE = "billing.manage"
    MEMBERS_MANAGE = "members.manage"
    DATA_MANAGE = "data.manage"
    INSIGHTS_VIEW = "insights.view"
    RECOMMENDATIONS_ACTION = "recommendations.action"
    ACTIONS_MANAGE = "actions.manage"


class KpiCategory(StrEnum):
    """Business areas a Manager's remit can be limited to (the health-score categories)."""

    FINANCIAL = "financial"
    SALES = "sales"
    CUSTOMER = "customer"
    MARKETING = "marketing"
    INVENTORY = "inventory"
    OPERATIONAL = "operational"


ROLE_CODES = ("owner", "manager", "viewer")
