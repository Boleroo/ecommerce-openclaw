from dataclasses import dataclass
from types import MappingProxyType


ROLE_PERMISSIONS = MappingProxyType({
    "viewer": frozenset({"business:read"}),
    "operator": frozenset({"business:read", "orders:refund", "products:price"}),
    "manager": frozenset({
        "business:read", "orders:refund", "products:price", "approvals:manager",
    }),
    "admin": frozenset({
        "business:read", "orders:refund", "products:price", "approvals:manager",
        "approvals:admin", "roles:manage",
    }),
})
REFUND_DIRECT_LIMIT = 10_000
REFUND_MANAGER_LIMIT = 50_000


@dataclass(frozen=True)
class RiskDecision:
    risk_level: str
    approval_level: str | None = None

    @property
    def approval_required(self):
        return self.approval_level is not None

    @property
    def approval_message(self):
        if self.approval_level == "admin":
            return "Admin approval is required"
        if self.approval_level == "manager":
            return "Manager or Admin approval is required"
        return None


def permissions_for_role(role):
    try:
        return ROLE_PERMISSIONS[role]
    except KeyError:
        raise ValueError("Unknown user role") from None


def classify_refund(amount):
    if amount <= REFUND_DIRECT_LIMIT:
        return RiskDecision("MEDIUM")
    if amount <= REFUND_MANAGER_LIMIT:
        return RiskDecision("HIGH", "manager")
    return RiskDecision("HIGH", "admin")


def classify_price_change(current_price, new_price):
    scaled_change = abs(new_price - current_price) * 100
    if scaled_change <= current_price * 5:
        return RiskDecision("MEDIUM")
    if scaled_change <= current_price * 20:
        return RiskDecision("HIGH", "manager")
    return RiskDecision("HIGH", "admin")
