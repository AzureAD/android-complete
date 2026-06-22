"""
Request-scoped user context using Python contextvars.

The auth middleware sets the user context per-request, and MCP tool
functions read it to get the user's identity and OBO-exchanged tokens.
"""

import contextvars
from dataclasses import dataclass, field
from typing import Optional

# Context variable — set per-request by auth middleware
_user_context_var: contextvars.ContextVar["UserContext"] = contextvars.ContextVar(
    "user_context", default=None
)


@dataclass
class UserContext:
    """Per-request user context populated by the auth middleware."""

    email: str = ""
    raw_token: str = ""  # Original Bearer token (for OBO exchange)
    claims: dict = field(default_factory=dict)
    _kusto_token: Optional[str] = None  # Cached OBO-exchanged Kusto token

    @property
    def kusto_token(self) -> Optional[str]:
        """Lazy OBO exchange: only exchange when first accessed."""
        if self._kusto_token is not None:
            return self._kusto_token

        if not self.raw_token:
            return None

        from android_dri_mcp_server.obo_exchanger import obo_exchanger

        if not obo_exchanger.enabled:
            return None

        self._kusto_token = obo_exchanger.exchange_token(self.raw_token)
        return self._kusto_token


def set_user_context(ctx: UserContext) -> contextvars.Token:
    """Set the user context for the current async task/request."""
    return _user_context_var.set(ctx)


def get_user_context() -> Optional[UserContext]:
    """Get the user context for the current request. Returns None if not set."""
    return _user_context_var.get()
