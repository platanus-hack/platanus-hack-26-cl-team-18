from __future__ import annotations

from uuid import uuid4

from app.modules.common.application.command import Command
from app.modules.common.application.message_bus import MessageBus


class _TestCommand(Command):
    @property
    def command_id(self):
        return uuid4()


class _HookUow:
    def __init__(self) -> None:
        self._committed = False
        self._has_writes = False
        self._post_commit_hooks: list[object] = []

    def __enter__(self) -> _HookUow:
        self._committed = False
        self._has_writes = False
        self._post_commit_hooks = []
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        del exc_type, exc, tb

    @property
    def committed(self) -> bool:
        return self._committed

    @property
    def has_writes(self) -> bool:
        return self._has_writes

    def commit(self):
        self._committed = True
        return ()

    def rollback(self) -> None:
        return None

    def run_post_commit_hooks(self) -> None:
        hooks = list(self._post_commit_hooks)
        self._post_commit_hooks.clear()
        for hook in hooks:
            hook()

    def register_post_commit_hook(self, hook) -> None:
        self._post_commit_hooks.append(hook)


def test_message_bus_raises_post_commit_hook_failure_when_strict() -> None:
    bus = MessageBus(
        uow_factory=_HookUow,
        command_handlers={
            _TestCommand: lambda cmd, uow: uow.register_post_commit_hook(
                lambda: (_ for _ in ()).throw(RuntimeError("hook failed"))
            )
        },
        event_handlers_by_type={},
        best_effort_events=False,
    )

    try:
        bus.handle(_TestCommand())
    except RuntimeError as exc:
        assert str(exc) == "hook failed"
    else:  # pragma: no cover
        raise AssertionError("Expected strict message bus to re-raise hook failure")


def test_message_bus_swallows_post_commit_hook_failure_when_best_effort() -> None:
    bus = MessageBus(
        uow_factory=_HookUow,
        command_handlers={
            _TestCommand: lambda cmd, uow: uow.register_post_commit_hook(
                lambda: (_ for _ in ()).throw(RuntimeError("hook failed"))
            )
        },
        event_handlers_by_type={},
        best_effort_events=True,
    )

    assert bus.handle(_TestCommand()) is None
