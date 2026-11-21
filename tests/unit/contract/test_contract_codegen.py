from __future__ import annotations

from pathlib import Path

from app.contract import messages
from app.contract.codegen import OUTPUT_PATH, render

ROOT = Path(__file__).resolve().parents[3]


def test_generated_ts_contract_is_up_to_date() -> None:
    """The web app's zod schemas must match the Pydantic contract: run `make contract`."""
    committed = (ROOT / OUTPUT_PATH).read_text(encoding="utf-8")
    assert committed == render(), "web/src/contract/messages.gen.ts is stale: run `make contract`"


def test_generated_ts_exports_every_message_and_socket_union() -> None:
    out = render()
    for model in messages.ALL_MESSAGES:
        assert f"export const {model.__name__}Schema = z.strictObject(" in out
        assert f"export type {model.__name__} = " in out
    for union in messages.SOCKET_UNIONS:
        assert f'export const {union}Schema = z.discriminatedUnion("type", [' in out


def test_wire_fields_are_never_optional_in_ts() -> None:
    """The backend emits every field; optionality would be a lie the UI must handle."""
    out = render()
    assert ".optional()" not in out
    assert ".default(" not in out


def test_render_is_deterministic() -> None:
    assert render() == render()
