from __future__ import annotations

import abc
from dataclasses import dataclass
from typing import Any, ClassVar, Self


@dataclass(frozen=True, slots=True)
class ValueObject(abc.ABC):
    """Base class for Value Objects (VOs).

    Contract:
    - VOs are immutable (frozen) and should be canonical.
    - VOs expose a `create(...)` factory as the *preferred* construction path.
      This factory may:
        * validate invariants
        * normalize/canonicalize representations (trim, case-fold, quantize, etc.)
      and must return a fully valid VO instance.

    Notes:
    - Keep factories *typed* in subclasses (avoid `*args/**kwargs` there).
    - Prefer passing already-validated VOs into Aggregate Roots.
    - Application layer calls `VO.create(...)` when translating DTOs.
    - Subclasses should implement a `_enforce_types()` method for invariant/type
      enforcement; some VOs may call it inside `create()` for early checks.

    """

    # Optional: subclasses can use this for consistent error messages
    _VO_NAME: ClassVar[str] = "ValueObject"

    @classmethod
    @abc.abstractmethod
    def create(cls, *args: Any, **kwargs: Any) -> Self:
        """Construct a valid VO instance.

        Subclasses SHOULD override with a typed signature, e.g.:

            @classmethod
            def create(cls, raw: str) -> "SegmentId": ...

        The variadic signature here exists only because Python can't enforce
        a single constructor signature across different VOs.
        """
        raise NotImplementedError

    @abc.abstractmethod
    def _enforce_types(self) -> None:
        """Enforce type/value invariants for direct construction."""
        raise NotImplementedError
