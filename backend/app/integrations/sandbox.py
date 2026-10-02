"""A pretend provider for development and tests, so the whole connect / sync / disconnect flow
can be tried with no Xero or Shopify account. It does not exist in staging or production."""

from datetime import timedelta

from app.core.config import Settings
from app.integrations.base import (
    AccountInfo,
    Provider,
    ProviderRejected,
    SyncContext,
    SyncOutcome,
    TokenSet,
    utcnow,
)


class SandboxProvider(Provider):
    key = "sandbox"
    label = "Sandbox (practice connection)"
    scopes = ("sandbox.read",)
    permissions = (
        "Pretend to read your sales (nothing real is read or changed).",
        "This only exists while testing Vyterlix.",
    )

    def is_configured(self, settings: Settings) -> bool:
        return settings.env in ("dev", "test")

    def authorize_url(self, *, state: str, redirect_uri: str, code_challenge: str) -> str:
        # A real provider shows its own consent page; the sandbox approves straight away.
        return f"{redirect_uri}?code=sandbox-code&state={state}"

    def exchange_code(self, *, code: str, redirect_uri: str, code_verifier: str) -> TokenSet:
        if code != "sandbox-code":
            raise ProviderRejected("That sign-in code isn't valid.", code="invalid_code")
        return self._tokens(None)

    def refresh(self, tokens: TokenSet) -> TokenSet:
        return self._tokens(tokens.refresh_token)

    def account(self, tokens: TokenSet) -> AccountInfo:
        return AccountInfo("sandbox-account-1", "Practice Bakery Ltd", {"practice": True})

    def sync(self, ctx: SyncContext) -> SyncOutcome:
        total = 3
        for done in range(1, total + 1):
            ctx.progress(done, total)
        return SyncOutcome(records_fetched=total, cursor={"since": ctx.now.isoformat()})

    @staticmethod
    def _tokens(refresh_token: str | None) -> TokenSet:
        return TokenSet(
            access_token="sandbox-access",
            refresh_token=refresh_token or "sandbox-refresh",
            expires_at=utcnow() + timedelta(hours=1),
            scopes=("sandbox.read",),
        )
