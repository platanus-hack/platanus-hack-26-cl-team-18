import asyncio
from datetime import UTC, datetime, timedelta
from io import BytesIO

from PIL import Image

from app.contract.messages import (
    AudienceKeyframe,
    RecognitionAttribute,
    RecognitionPerson,
    RecognitionSnapshot,
)
from app.modules.audience.infrastructure.recognition_store import RecognitionStore
from app.modules.audience.infrastructure.recognition_vlm import (
    RecognitionVlm,
    VisualDetail,
    VisualDetails,
    has_opaque_head_redaction,
)
from app.realtime.router import InboundRouter, Received


def image(redacted: bool) -> bytes:
    frame = Image.new("RGB", (120, 200), "white")
    if redacted:
        frame.paste("black", (0, 0, 120, 100))
    out = BytesIO()
    frame.save(out, format="JPEG")
    return out.getvalue()


def test_only_accepts_decodable_images_with_opaque_upper_band():
    assert has_opaque_head_redaction(image(True))
    assert not has_opaque_head_redaction(image(False))
    assert not has_opaque_head_redaction(b"invalid")


def test_late_enrichment_stays_on_original_session_and_survives_new_snapshot():
    t = datetime(2026, 1, 1, tzinfo=UTC)
    store = RecognitionStore()
    person = RecognitionPerson(
        track_id="1",
        first_seen_s=0,
        last_seen_s=2,
        box=[0, 0, 100, 200],
        confidence=0.9,
        direction="unknown",
        currently_visible=False,
    )
    data = {
        "screen_id": "test",
        "session_id": "one",
        "session_started_at": t,
        "seq": 1,
        "measured_at": t,
        "video_time_s": 3,
        "people": [],
        "history": [person],
        "objects": [],
        "audio_events": [],
        "latency": {"decode_ms": 0, "detection_tracking_ms": 0, "publication_delay_ms": 0},
    }
    first = RecognitionSnapshot(**data)
    store.put(first, Received(received_at=t))
    attr = RecognitionAttribute(
        name="brand",
        value="Nike",
        source="vlm",
        observed_at_s=1,
        evidence="visible logo",
        uncertainty="medium",
        model="test@low",
    )
    assert not store.enrich("test", "other", "1", 1, [attr])
    assert store.enrich("test", "one", "1", 1, [attr])
    weak = attr.model_copy(update={"value": "other", "observed_at_s": 2, "uncertainty": "high"})
    assert store.enrich("test", "one", "1", 2, [weak])
    store.put(first.model_copy(update={"seq": 2}), Received(received_at=t))
    snapshot, _ = store.get("test", t)
    assert snapshot.people == []
    assert snapshot.history[0].attributes == [attr]


def test_vlm_is_bounded_and_late_result_enriches_history():
    t = datetime(2026, 1, 1, tzinfo=UTC)
    store = RecognitionStore()
    person = RecognitionPerson(
        track_id="1",
        first_seen_s=0,
        last_seen_s=2,
        box=[0, 0, 100, 200],
        confidence=0.9,
        direction="unknown",
        currently_visible=False,
    )
    snapshot = RecognitionSnapshot(
        screen_id="test",
        session_id="one",
        session_started_at=t,
        seq=1,
        measured_at=t,
        video_time_s=3,
        people=[],
        history=[person],
        objects=[],
        audio_events=[],
        latency={"decode_ms": 0, "detection_tracking_ms": 0, "publication_delay_ms": 0},
    )
    store.put(snapshot, Received(received_at=t))

    class FakeAI:
        async def structured_output(self, *args, **kwargs):
            return VisualDetails(
                details=[
                    VisualDetail(
                        kind="brand",
                        value="Nike",
                        evidence="visible logo",
                        uncertainty="medium",
                        associated_item="hoodie",
                    )
                ]
            )

    class FakeWorker:
        def submit_threadsafe(self, provider, key, job):
            self.job = job

    worker = FakeWorker()
    router = InboundRouter()
    seen = []
    router.on(RecognitionSnapshot, lambda message, _received: seen.append(message))
    vlm = RecognitionVlm(FakeAI(), "test", worker, store, router)
    jpeg = image(True)
    header = AudienceKeyframe(
        screen_id="test",
        session_id="one",
        track_id="1",
        video_time_s=1,
        seq=1,
        measured_at=t,
        byte_length=len(jpeg),
    )
    vlm.accept(header, Received(received_at=t, payload=jpeg))
    asyncio.run(worker.job())
    result, _ = store.get("test", t)
    assert result.people == []
    assert result.history[0].attributes[0].value == "Nike"
    assert result.revision == 1
    assert seen[0].history[0].attributes[0].value == "Nike"


def test_old_session_cannot_replace_new_session():
    t = datetime(2026, 1, 1, tzinfo=UTC)
    store = RecognitionStore()
    first = RecognitionSnapshot(
        screen_id="test",
        session_id="old",
        session_started_at=t,
        seq=1,
        measured_at=t,
        video_time_s=0,
        latency={"decode_ms": 0, "detection_tracking_ms": 0, "publication_delay_ms": 0},
    )
    second = first.model_copy(
        update={"session_id": "new", "session_started_at": t + timedelta(seconds=1)}
    )
    store.put(first, Received(received_at=t))
    store.put(second, Received(received_at=t + timedelta(seconds=1)))
    store.put(first.model_copy(update={"seq": 2}), Received(received_at=t + timedelta(seconds=2)))
    snapshot, _ = store.get("test", t + timedelta(seconds=2))
    assert snapshot.session_id == "new"
