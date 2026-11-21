"""Contract tests for `TypedId`.

These matter more than usual: the architecture gate that checks value objects
globs `app/modules/*/domain/value_objects/*.py` and only fires on classes whose
direct base is literally `ValueObject`, so neither this base nor any concrete
id subclass is covered by CI. The base has to be right on its own.
"""

from __future__ import annotations

import dataclasses
from uuid import UUID, uuid4

import pytest

from app.modules.common.domain.primitives.typed_id import TypedId


class _ScreenId(TypedId):
    __slots__ = ()


class _CampaignId(TypedId):
    __slots__ = ()


def test_wraps_a_uuid() -> None:
    raw = uuid4()
    assert _ScreenId(raw).value == raw
    assert _ScreenId.create(raw).value == raw


def test_create_accepts_the_canonical_string_form() -> None:
    raw = uuid4()
    assert _ScreenId.create(f"  {raw} ") == _ScreenId(raw)


def test_create_rejects_a_malformed_string() -> None:
    with pytest.raises(ValueError, match="Invalid _ScreenId"):
        _ScreenId.create("not-a-uuid")


def test_create_rejects_a_non_uuid_type() -> None:
    with pytest.raises(TypeError, match="must be built from UUID or str"):
        _ScreenId.create(42)  # type: ignore[arg-type]


def test_direct_construction_still_enforces_the_type() -> None:
    with pytest.raises(TypeError, match="must be UUID"):
        _ScreenId("00000000-0000-0000-0000-000000000000")  # type: ignore[arg-type]


def test_the_base_class_is_not_instantiable() -> None:
    with pytest.raises(TypeError, match="abstract"):
        TypedId(uuid4())


def test_same_type_same_uuid_is_equal() -> None:
    raw = uuid4()
    assert _ScreenId(raw) == _ScreenId(raw)
    assert hash(_ScreenId(raw)) == hash(_ScreenId(raw))


def test_different_id_types_never_collide() -> None:
    raw = uuid4()
    assert _ScreenId(raw) != _CampaignId(raw)
    assert len({_ScreenId(raw), _CampaignId(raw)}) == 2


def test_a_bare_uuid_is_not_equal_to_a_typed_id() -> None:
    raw = uuid4()
    assert _ScreenId(raw) != raw
    assert raw != _ScreenId(raw)


def test_is_immutable() -> None:
    identifier = _ScreenId(uuid4())
    with pytest.raises(dataclasses.FrozenInstanceError):
        identifier.value = uuid4()  # type: ignore[misc]


def test_string_form_round_trips() -> None:
    identifier = _ScreenId(uuid4())
    assert _ScreenId.create(str(identifier)) == identifier
    assert UUID(str(identifier)) == identifier.value


def test_repr_names_the_id_type() -> None:
    identifier = _ScreenId(UUID("11111111-1111-1111-1111-111111111111"))
    assert repr(identifier) == "_ScreenId('11111111-1111-1111-1111-111111111111')"


def test_subclasses_carry_no_instance_dict() -> None:
    assert not hasattr(_ScreenId(uuid4()), "__dict__")
