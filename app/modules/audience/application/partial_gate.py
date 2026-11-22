"""Which spoken partials the backend may act on (ADR 0005, product invariant 2).

Realtime partials are revised freely, and keyterm biasing makes the recognizer
invent exactly our bias words in crowd noise ("Shop", "Sopaipilla"). Real
words persist across partials; inventions flicker. The edge agent stabilizes
its own partials before sending (`edge/agent/speech/pipeline.py`); the /screen
sensor sends raw ones (`partials_stabilized=False`), so the backend applies the
same rule to them: a partial's meaning counts only once `stability`
consecutive partials of the utterance agree, and an unchanged meaning is not
acted on twice.

Age-restricted intents are final-only on raw partials: a partial whose
meaning *wants* one is never acted on, because stabilization still lets an
invention through now and then (the edge agent already holds these back
itself). Rejecting one early stays allowed: it can only make an ad safer.
Finals and pre-stabilized partials always pass.

Holds meanings only (canonical keyterms), never utterance text.
"""

from __future__ import annotations

type Meaning = tuple[tuple[str, ...], tuple[str, ...]]  # (wanted, rejected)

_NOTHING: Meaning = ((), ())


class PartialGate:
    def __init__(
        self, *, stability: int = 2, final_only: frozenset[str] = frozenset({"cerveza"})
    ) -> None:
        if stability < 1:
            raise ValueError("stability must be at least 1")
        self._stability = stability
        self._final_only = final_only
        self._utterance: str | None = None
        self._acted: Meaning = _NOTHING
        self._candidate: Meaning | None = None
        self._seen = 0

    def admit(
        self,
        *,
        utterance_id: str | None,
        is_final: bool,
        stabilized: bool,
        wanted: tuple[str, ...],
        rejected: tuple[str, ...],
    ) -> bool:
        if is_final:
            self._reset(None)
            return True
        if stabilized:
            return True
        if self._final_only.intersection(wanted):
            return False
        if utterance_id is None or utterance_id != self._utterance:
            self._reset(utterance_id)
        current: Meaning = (wanted, rejected)
        if current == self._candidate:
            self._seen += 1
        else:
            self._candidate, self._seen = current, 1
        if current == self._acted or self._seen < self._stability:
            return False
        self._acted = current
        return True

    def _reset(self, utterance_id: str | None) -> None:
        self._utterance = utterance_id
        self._acted = _NOTHING
        self._candidate, self._seen = None, 0
