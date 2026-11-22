"""Cross-boundary failures over real HTTP and sockets, with deterministic time."""

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from itertools import permutations

import pytest
from fastapi.testclient import TestClient
from scripts.replay_audience import paid_brands, snapshot_for

from app.bootstrap.container import build_container
from app.config.settings import AppSettings
from app.contract.messages import (
    AudienceSnapshot,
    ConsoleReceipt,
    CreativeReady,
    PlaybackReport,
    PlayerDisplayed,
)
from app.main import create_app
from app.modules.common.infrastructure.clock.fixed_clock import FixedClock
from tests.unit.api.test_auction_loop_e2e import T0, Loop, loop as loop, slide
from tests.unit.api.test_ws_gateway import Recorder


@pytest.mark.parametrize("after_append", [False, True])
def test_same_socket_can_retry_a_failed_billing_record_without_a_second_charge(
    loop: Loop,
    monkeypatch: pytest.MonkeyPatch,
    after_append: bool,
) -> None:
    loop.send("fitness")
    loop.auction()
    creative = loop.creative()
    end = loop.clock.advance(timedelta(seconds=4))
    report = PlaybackReport(
        screen_id="demo-01",
        creative_id=creative.creative_id,
        seq=1,
        started_at=creative.trace.ready_at,
        ended_at=end,
    )
    original = loop.container.ledger.append
    failed = threading.Event()

    def interrupt(charges):  # type: ignore[no-untyped-def]
        if not failed.is_set():
            failed.set()
            if after_append:
                original(charges)
            raise ConnectionError("injected append failure")
        original(charges)

    monkeypatch.setattr(loop.container.ledger, "append", interrupt)
    routed = Recorder()
    loop.container.router.on(PlaybackReport, routed)
    loop.player.send_text(report.model_dump_json())
    routed.wait_for(1)
    assert failed.is_set()
    loop.player.send_text(report.model_dump_json())
    routed.wait_for(2)
    receipt = loop.next_of(ConsoleReceipt)
    assert receipt.total.amount == "26"
    assert len(loop.container.ledger.charges()) == 1
    spent = [
        line
        for line in loop.client.get("/api/book").json()["lines"]
        if line["brand_name"] == "Voltio"
    ]
    assert spent[0]["spent"]["amount"] == "26"


@pytest.mark.parametrize("skew", [-3600, 3600])
def test_player_clock_skew_cannot_move_display_holds_outside_server_time(
    loop: Loop, skew: int
) -> None:
    loop.send("fitness")
    loop.auction()
    creative = loop.creative()
    ready = loop.clock.now_utc()
    routed = Recorder()
    loop.container.router.on(PlayerDisplayed, routed)
    loop.player.send_text(
        PlayerDisplayed(
            screen_id="demo-01",
            creative_id=creative.creative_id,
            seq=1,
            displayed_at=ready + timedelta(seconds=skew),
        ).model_dump_json()
    )
    routed.wait_for(1)
    assert slide(loop, "Rayo", "2", 1).status_code == 200
    decision = loop.auction()
    assert decision.trace.swap_not_before == ready + timedelta(seconds=3)


def test_repeated_display_confirmation_does_not_extend_the_minimum_ad_hold(loop: Loop) -> None:
    loop.send("fitness")
    loop.auction()
    creative = loop.creative()
    first_display = loop.clock.advance(timedelta(milliseconds=250))
    routed = Recorder()
    loop.container.router.on(PlayerDisplayed, routed)
    for seq in (1, 2):
        loop.player.send_text(
            PlayerDisplayed(
                screen_id="demo-01",
                creative_id=creative.creative_id,
                seq=seq,
                displayed_at=loop.clock.now_utc(),
            ).model_dump_json()
        )
        routed.wait_for(seq)
        loop.clock.advance(timedelta(seconds=1))
    assert slide(loop, "Rayo", "2", 1).status_code == 200
    decision = loop.auction()
    assert decision.trace.swap_not_before == first_display + timedelta(seconds=3)


@pytest.mark.parametrize("events", list(permutations(("bid", "stock", "minor"))))
def test_two_screen_journey_keeps_billing_and_redemption_correct_in_every_operator_order(
    tmp_path,
    events: tuple[str, ...],
) -> None:
    clock = FixedClock(T0)
    container = build_container(
        AppSettings(
            _env_file=None,
            app_env="test",
            media_dir=tmp_path,
            auction_swap_holds=False,
            audience_ai=False,
            creative_ai_copy=False,
            voiceovers_enabled=False,
        ),
        clock=clock,
    )
    with (
        TestClient(create_app(container=container)) as client,
        client.websocket_connect("/ws/screen/north") as north,
        client.websocket_connect("/ws/screen/south") as south,
        client.websocket_connect("/ws/agent") as agent,
    ):
        north.receive_json(), south.receive_json()
        first = {}
        for screen, scene, player in (("north", "co-brand", north), ("south", "adult", south)):
            now = clock.advance(timedelta(milliseconds=100))
            snapshot = snapshot_for(scene, 1, now).model_copy(update={"screen_id": screen})
            agent.send_text(snapshot.model_dump_json())
            first[screen] = CreativeReady.model_validate(player.receive_json())
        assert set(paid_brands(first["north"])) == {"Voltio", "Café Patio"}
        assert paid_brands(first["south"]) == ("Malta Sur",)
        clock.advance(timedelta(seconds=4))
        book = client.get("/api/book?screen_id=north").json()["lines"]
        rayo = next(line for line in book if line["brand_name"] == "Rayo")
        cafe = next(line for line in book if line["brand_name"] == "Café Patio")
        changed = set()
        latest = first["north"]
        for event in events:
            changed.add(event)
            if event == "minor":
                snapshot = snapshot_for(
                    "minor-safe", 2, clock.advance(timedelta(milliseconds=100))
                ).model_copy(update={"screen_id": "south"})
                agent.send_text(snapshot.model_dump_json())
                assert paid_brands(CreativeReady.model_validate(south.receive_json())) == ()
                continue
            if event == "bid":
                response = client.post(
                    "/api/advertiser/bid-multiplier",
                    json={"campaign_id": rayo["campaign_id"], "multiplier": "1.5", "seq": 1},
                )
            else:
                response = client.post(
                    "/api/clerk/stock",
                    json={"screen_id": "north", "brand_id": cafe["brand_id"], "in_stock": False},
                )
            assert response.status_code == 200
            latest = CreativeReady.model_validate(north.receive_json())
            expected = {"Rayo" if "bid" in changed else "Voltio"}
            if "stock" not in changed:
                expected.add("Café Patio")
            assert set(paid_brands(latest)) == expected

        for screen, expected in (("north", ("Rayo",)), ("south", ())):
            with client.websocket_connect(f"/ws/screen/{screen}") as reconnect:
                assert (
                    paid_brands(CreativeReady.model_validate(reconnect.receive_json())) == expected
                )
        for screen, stocked in (("north", False), ("south", True)):
            lines = client.get(f"/api/book?screen_id={screen}").json()["lines"]
            assert (
                next(line for line in lines if line["brand_name"] == "Café Patio")["in_stock"]
                is stocked
            )

        # A fresh seq cannot resurrect an old adult moment after the minor appeared.
        observed = Recorder()
        container.router.on(AudienceSnapshot, observed)
        stale = snapshot_for("adult", 3, T0).model_copy(update={"screen_id": "south"})
        agent.send_text(stale.model_dump_json())
        # 4 s late against this link's normal: the gateway's freshness gate drops it
        # (app/realtime/freshness.py) before the loop's own ordering ever has to.
        agent.send_text("{}")
        assert agent.receive_json()["type"] == "system.error"
        assert container.freshness.dropped == 1 and observed.items == []
        with client.websocket_connect("/ws/screen/south") as reconnect:
            assert paid_brands(CreativeReady.model_validate(reconnect.receive_json())) == ()

        # Reporting another screen's play cannot charge it; the same socket survives.
        report = PlaybackReport(
            screen_id="north",
            creative_id=first["north"].creative_id,
            seq=1,
            started_at=first["north"].trace.ready_at,
            ended_at=first["north"].trace.ready_at + timedelta(seconds=4),
        )
        south.send_text(report.model_dump_json())
        assert south.receive_json()["code"] == "message.screen_mismatch"
        billed = Recorder()
        container.router.on(PlaybackReport, billed)
        north.send_text(report.model_dump_json())
        billed.wait_for(1)
        north.send_text(report.model_dump_json())
        billed.wait_for(2)
        charges = container.ledger.charges()
        assert len(charges) == 2
        assert {charge.screen_id for charge in charges} == {"north"}
        assert [charge.amount.amount for charge in charges] == [18, 10]
        assert {charge.auction_id for charge in charges} == {first["north"].auction_id}

        code = first["north"].spec.redemption_code
        assert code is not None and client.get(f"/r/{code.lower()}").status_code == 200

        def redeem(_):  # type: ignore[no-untyped-def]
            return client.post(f"/r/{code}/redeem", json={"nonce": "same-anonymous-phone"}).json()

        with ThreadPoolExecutor(max_workers=4) as workers:
            results = list(workers.map(redeem, range(16)))
        assert sum(result["new"] for result in results) == 1
        assert all(set(result["brands"]) == {"Voltio", "Café Patio"} for result in results)
        clock.set(first["north"].trace.ready_at + timedelta(minutes=10))
        assert (
            client.post(f"/r/{code}/redeem", json={"nonce": "new-anonymous-phone"}).status_code
            == 410
        )
        assert redeem(0)["new"] is False
