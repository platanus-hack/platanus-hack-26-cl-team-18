"""Worker threads read who is watching from counts, never from the loop-owned sets."""

from __future__ import annotations

import threading

from app.realtime.hub import ConnectionHub


class Sink:
    async def send_text(self, data: str) -> None:
        return None


def test_station_and_dev_counts_follow_every_registration() -> None:
    hub = ConnectionHub()
    raw, station, dev, player = Sink(), Sink(), Sink(), Sink()
    assert not hub.has_station_consoles() and hub.dev_count() == 0
    hub.add_console(raw)  # a raw reader (no ?live=1) is not a station console
    assert not hub.has_station_consoles()
    hub.add_player("demo-01", player, live=True)  # a live player is not one either
    assert not hub.has_station_consoles()
    hub.add_console(station, live=True)
    assert hub.has_station_consoles()
    hub.remove_console(station)
    assert not hub.has_station_consoles()
    hub.add_dev(dev)
    assert hub.dev_count() == 1 and hub.has_station_consoles()
    hub.remove_dev(dev)
    assert hub.dev_count() == 0 and not hub.has_station_consoles()


def test_reads_from_worker_threads_never_race_set_mutation() -> None:
    hub = ConnectionHub()
    errors: list[BaseException] = []
    stop = threading.Event()

    def read() -> None:
        try:
            while not stop.is_set():
                hub.has_station_consoles()
                hub.dev_count()
        except BaseException as exc:  # "Set changed size during iteration" before the fix
            errors.append(exc)

    readers = [threading.Thread(target=read) for _ in range(4)]
    for reader in readers:
        reader.start()
    sinks = [Sink() for _ in range(200)]
    for _ in range(20):
        for sink in sinks:
            hub.add_console(sink, live=True)
            hub.add_dev(sink)
        for sink in sinks:
            hub.remove_console(sink)
            hub.remove_dev(sink)
    stop.set()
    for reader in readers:
        reader.join()
    assert errors == []
    assert not hub.has_station_consoles() and hub.dev_count() == 0
