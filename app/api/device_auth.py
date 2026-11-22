"""Who may feed the live loop: the perception agent and billing players.

`/ws/agent` (audience facts → auctions) and the reports a player sends on
`/ws/screen/{id}` (playback.report → charges) move money, so they need a device
token. Watching a screen does not: a player without the screen token still gets
every creative (view-only), but its reports are refused, so it can never bill.

- Agent: `Authorization: Bearer <AGENT_TOKEN>` (a header, so it never lands in
  an access log). The browser sensor in `/screen` (ADR 0010) offers the SCREEN_TOKEN
  in the subprotocol list instead, like the player.
- Player: browsers can't set WebSocket headers, so the token rides in the one
  they can, the subprotocol list: `["team18", "team18.token.<SCREEN_TOKEN>"]`.
  The server answers with `team18` and never echoes the token.
- Console: `/ws/console` streams every bid, charge and budget. With
  `CONSOLE_TOKEN` set it is offered the same way as the screen token; unset,
  the console stays open (opt-in, so the station works as before).

A device token that is unset means open outside prod (local station, tests, evals) and
closed in prod. In prod a token must have at least `PROD_MIN_TOKEN_LENGTH`
characters, so the dashboard placeholder can't unlock anything. Comparisons are
constant time.
"""

from __future__ import annotations

import hmac

from pydantic import SecretStr
from starlette.websockets import WebSocket

from app.config.settings import AppSettings

PROD_MIN_TOKEN_LENGTH = 24  # `openssl rand -hex 16` gives 32
SUBPROTOCOL = "team18"
TOKEN_SUBPROTOCOL_PREFIX = "team18.token."


def token_allowed(settings: AppSettings, expected: SecretStr | None, presented: str | None) -> bool:
    secret = expected.get_secret_value() if expected is not None else ""
    if not secret:
        return settings.app_env != "prod"
    if settings.app_env == "prod" and len(secret) < PROD_MIN_TOKEN_LENGTH:
        return False
    if not presented:
        return False
    return hmac.compare_digest(presented.encode(), secret.encode())


def bearer_token(ws: WebSocket) -> str | None:
    header = ws.headers.get("authorization", "")
    scheme, _, value = header.partition(" ")
    return value.strip() or None if scheme.lower() == "bearer" else None


def offered_subprotocols(ws: WebSocket) -> list[str]:
    return [str(item) for item in ws.scope.get("subprotocols", [])]


def subprotocol_token(ws: WebSocket) -> str | None:
    for item in offered_subprotocols(ws):
        if item.startswith(TOKEN_SUBPROTOCOL_PREFIX):
            return item.removeprefix(TOKEN_SUBPROTOCOL_PREFIX) or None
    return None


def accepted_subprotocol(ws: WebSocket) -> str | None:
    """Echo `team18` when offered (a browser fails the handshake otherwise)."""
    return SUBPROTOCOL if SUBPROTOCOL in offered_subprotocols(ws) else None


def agent_allowed(settings: AppSettings, ws: WebSocket) -> bool:
    """The edge agent's bearer AGENT_TOKEN, or the browser sensor in `/screen` (ADR 0010):
    a browser can't set headers, so it offers the SCREEN_TOKEN as a subprotocol."""
    if token_allowed(settings, settings.agent_token, bearer_token(ws)):
        return True
    presented = subprotocol_token(ws)
    return presented is not None and token_allowed(settings, settings.screen_token, presented)


def screen_can_report(settings: AppSettings, ws: WebSocket) -> bool:
    return token_allowed(settings, settings.screen_token, subprotocol_token(ws))


def console_allowed(settings: AppSettings, ws: WebSocket) -> bool:
    """`/ws/console` streams every bid, charge and budget. Opt-in: with CONSOLE_TOKEN set
    the console must offer it like a player (`team18.token.<CONSOLE_TOKEN>`); unset, the
    socket stays open in every environment (the station console as it always was)."""
    secret = settings.console_token.get_secret_value() if settings.console_token else ""
    if not secret:
        return True
    presented = subprotocol_token(ws)
    return presented is not None and hmac.compare_digest(presented.encode(), secret.encode())
