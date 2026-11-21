"""Size-guardrail accounting for the shared ``Metadata`` value object.

A real failure: a scheduled job dead-looped on an aggregate whose metadata
snapshot serialized to ~11 KB of JSON, yet
``Metadata.create`` rejected it as ``metadata too large (max_total_size=64000)``.

The estimator re-measured every container's whole subtree when it closed, on
top of the leaves already counted on the way down, so a scalar at depth *d* was
charged roughly *d + 1* times. At the snapshot's depth of six that made
the effective ceiling ~9 KB, not the documented 64 KB.

These tests pin that every scalar and key is charged exactly once, so the
ceiling means the same thing regardless of how deeply a payload nests.
"""

from __future__ import annotations

import json

import pytest

from app.modules.common.domain.value_objects.metadata import Metadata, MetadataError


def _nested(leaf: str, *, depth: int) -> object:
    value: object = leaf
    for _ in range(depth):
        value = [{"k": value}]
    return value


def test_nesting_does_not_multiply_the_charged_size() -> None:
    """A ~12 KB payload nested six deep must fit a 64 KB ceiling."""
    payload = {
        "phases": [{"items": [{"sources": ["x" * 110 for _ in range(30)]}]} for _ in range(3)]
    }
    assert len(json.dumps(payload)) < 12_000

    metadata = Metadata.create(payload)

    assert metadata.to_plain() == payload


def test_the_charged_size_is_independent_of_depth() -> None:
    """The same leaf bytes cost the same whether shallow or deep."""
    leaf = "y" * 3_000
    shallow = {"items": [leaf] * 18}  # ~54 KB of leaves
    deep = {"items": [_nested(leaf, depth=2) for _ in range(18)]}

    Metadata.create(shallow)
    Metadata.create(deep)


def test_payloads_genuinely_over_the_ceiling_are_still_rejected() -> None:
    too_big = {"items": ["z" * 4_000 for _ in range(17)]}  # 68 KB of leaves

    with pytest.raises(MetadataError, match="metadata too large"):
        Metadata.create(too_big)


def _oversized() -> dict[str, object]:
    return {"lines": [f"{index:04d}" + "x" * 1_000 for index in range(80)]}


def test_a_new_write_over_the_size_cap_is_rejected() -> None:
    with pytest.raises(MetadataError, match="too large"):
        Metadata.create(_oversized())


def test_a_stored_row_over_the_size_cap_still_loads() -> None:
    """A later release may write larger rows; older readers must keep loading them."""
    loaded = Metadata.from_persisted(_oversized())

    assert loaded.to_plain() == _oversized()


@pytest.mark.parametrize(
    "stored",
    [
        {"deep": {"a": {"b": {"c": {"d": {"e": {"f": {"g": 1}}}}}}}},
        {"long": "x" * (Metadata.MAX_STR_LEN + 1)},
        {f"k{index}": index for index in range(Metadata.MAX_KEYS + 1)},
        {"nan": float("nan")},
    ],
)
def test_a_stored_row_keeps_every_other_guardrail(stored: dict[str, object]) -> None:
    with pytest.raises(MetadataError):
        Metadata.from_persisted(stored)
