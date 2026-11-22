# Privacy: concerns, where the code enforces each answer, and open gaps

> **Doc status: LIVE.** The promises are in [`product.md` §7](product.md#7-privacy-and-trust-are-product-features)
> and product invariant 3 in `CLAUDE.md`. This page is the **evidence**: for each
> concern a judge, buyer or regulator raises, the file that enforces the answer
> and the test that fails if it breaks. Keep it honest: a claim without a test
> goes under §3 (gaps), not §2.

Verified on 2026-11-22: every test cited in §2 passes. G1–G4 were fixed that day
(§3 keeps the record).

```bash
uv run pytest tests/unit/audience/test_recognition_vlm.py \
  tests/unit/audience/test_keyframe_redaction.py tests/unit/audience/test_classifier.py \
  tests/unit/audience/test_catalog_and_claude_tagging.py \
  tests/unit/api/test_live_switch_flow.py tests/unit/api/test_redeem_page.py \
  tests/unit/auction/test_bundle.py::test_minor_likely_only_blocks_age_restricted_bids \
  tests/unit/creative/test_copy_guard.py tests/unit/creative/test_guard_regulatory.py \
  tests/unit/architecture
(cd edge && uv run pytest tests/test_vision_opencv.py tests/test_vocabulary.py tests/test_vision.py \
  tests/test_live_demo_failures.py tests/test_speech_pipeline.py::test_utterance_text_never_reaches_the_logs)
(cd gpu && uv run pytest tests/test_service.py::test_frames_are_never_written_or_logged)
```

## 1. Where personal data could flow

| Data | Leaves the camera laptop? | Goes to | Stored? |
|---|---|---|---|
| Raw camera frames | Yes, over TLS | **Our own** GPU service (ADR 0007) | No: processed in memory, never written |
| Scene keyframe, **allow-list**: black except detected people and objects, heads black, 512 px | Yes | Backend (checks the redaction) → Claude (`AudienceTagger`) | No |
| Console camera preview (demo only): whole frame, heads black | Yes | Our own console | No |
| One person's crop, head **blacked out** | Yes | Backend → Claude (held-product recognition) | No |
| Boxes, pose-derived flags (looking, distance band, `minor_likely`) | Yes | Backend (audience moment) | Only as per-auction aggregates |
| Mic audio (speech mode only) | Yes | ElevenLabs Scribe (STT) | No |
| Utterance text (speech mode only) | Yes | Backend → keyterm matcher, Claude `TopicExtractor` | No: only IAB topics with a TTL |
| QR redemption | n/a | Backend | Code + **hash** of a device nonce |

## 2. Concern → enforcement → test

### Identity and faces

| Concern | Enforced by | Test |
|---|---|---|
| "Do you recognise faces?" No face model, no embeddings, no re-identification. Tracks are a session counter (`t1`, `t2`, …), never linked across sessions or screens | [`edge/agent/vision/tracking.py`](../edge/agent/vision/tracking.py) (module docstring, `reset()`) | Stack ban in `docs/stack.md` (no face or head-pose model) |
| A face never reaches a third party, even one the detector missed | [`allowlist_keyframe`](../edge/agent/vision/capture.py) starts from black and copies only the regions [`keyframe_regions`](../edge/agent/vision/pipeline.py) vouches for (person boxes, objects and markers padded 10 %), then blacks out every head (opaque), including heads from a second, high-recall pose pass ([`HighRecallPeople`](../edge/agent/vision/adapters.py)). [`head_box`](../edge/agent/vision/geometry.py) falls back to the top of the person box when pose is uncertain | `edge/tests/test_crowded_hall.py::test_keyframe_hides_faces_the_hot_path_detector_missed`, `edge/tests/test_vision_opencv.py::test_allowlist_keyframe_hides_a_person_the_detector_missed`, `::test_allowlist_keyframe_of_nothing_is_all_black`, `edge/tests/test_vision.py::test_keyframe_keeps_people_and_padded_objects_and_masks_every_head`, `edge/tests/test_live_demo_failures.py::test_head_box_with_face_keypoints_outside_the_person_box_still_blurs_the_top` |
| The backend doesn't trust the agent's redaction | The header declares `redaction="allowlist"`, `kept` and `heads`; [`allowlist_redaction_holds`](../app/modules/audience/infrastructure/keyframe_redaction.py) checks the pixels (black outside `kept`, black inside every head) and the classifier drops any mismatch before the VLM. **Fail closed:** a classifier built without the check refuses every keyframe ([`classifier.py`](../app/modules/audience/application/classifier.py), `reject_every_keyframe`) | `tests/unit/audience/test_keyframe_redaction.py` (raw frame, missed person, visible head, missing declaration, non-JPEG all refused), `tests/unit/audience/test_classifier.py::test_a_keyframe_failing_the_redaction_check_never_reaches_the_tagger`, `::test_without_a_redaction_check_every_keyframe_is_refused` |
| The console preview hides faces | [`head_masked_frame`](../edge/agent/vision/capture.py): opaque, not a blur | `edge/tests/test_vision_opencv.py::test_preview_blacks_out_the_head_region_and_downscales` |
| Product recognition can't leak a face | [`redact_crop`](../edge/agent/vision/frame_selector.py) blacks out the head and upper band (opaque, not blur), hides overlapping people, and sends nothing when the face evidence is ambiguous. The **backend re-checks** it: [`has_opaque_head_redaction`](../app/modules/audience/infrastructure/recognition_vlm.py) drops any crop without the black band | `tests/unit/audience/test_recognition_vlm.py::test_only_accepts_decodable_images_with_opaque_upper_band` |
| Frames are never stored | The GPU service keeps shapes, not pixels | `gpu/tests/test_service.py::test_frames_are_never_written_or_logged` (patches `open`/`PIL.save`; asserts no writes, no bytes in app state, no JPEG in logs) |

### Forbidden inferences (race, religion, health, orientation, politics, precise age, emotion)

| Concern | Enforced by | Test |
|---|---|---|
| The VLM can't return an attribute of a person | Structured output with **no person fields**; segment ids are `Literal` enums from the catalog, `extra="forbid"` ([`tagging/schemas.py`](../app/modules/audience/infrastructure/tagging/schemas.py)) | `tests/unit/audience/test_catalog_and_claude_tagging.py::test_keyframe_schema_enumerates_the_catalog_and_has_no_person_fields` |
| The VLM only describes objects, never the person (clothes, body, hair) | System prompt in [`recognition_vlm.py`](../app/modules/audience/infrastructure/recognition_vlm.py) (`SYSTEM`) | Prompt text; see gap G6 |
| Segments are interests and purchase intent only | The catalog | `test_catalog_and_claude_tagging.py::test_catalog_is_interest_and_purchase_intent_only`, `::test_catalog_rejects_cues_outside_it_and_duplicate_ids` |
| Free-text labels never reach the auction | The anti-corruption layer drops unknown labels | `tests/unit/audience/test_classifier.py::test_sound_and_vision_compound_and_unknown_labels_are_dropped` |
| The open-vocabulary detector is never prompted about people | [`edge/agent/vision/vocabulary.py`](../edge/agent/vision/vocabulary.py) | `edge/tests/test_vocabulary.py::test_prompts_are_unique_and_never_name_a_person` |

### Minors

| Concern | Enforced by | Test |
|---|---|---|
| `minor_likely` only **blocks** and never targets | [`NoAgeRestrictedCategoryWhenMinorLikely`](../app/modules/auction/domain/rules.py), a pure `Specification` that excludes with reason `SAFETY` and changes no other bid | `tests/unit/auction/test_bundle.py::test_minor_likely_only_blocks_age_restricted_bids`, `tests/unit/catalog_sweep/test_props_fill_sweep.py::test_no_age_restricted_brand_wins_when_a_minor_is_likely` |
| Safety holds when the engine fails | Falls back to the house ad | `tests/unit/api/test_auction_loop_e2e.py::test_engine_down_while_beer_is_held_never_shows_it_to_a_minor`, `::test_engine_down_when_a_minor_meets_a_beer_ad_on_air_falls_back_to_house`, `::test_minor_in_front_of_beer_swaps_immediately` |
| Copy never appeals to children or names alcohol near a minor (Ley 20.606) | [`creative/domain/guard.py`](../app/modules/creative/domain/guard.py) | `tests/unit/creative/test_guard_regulatory.py::test_child_appeal_is_blocked_when_a_minor_is_likely`, `tests/unit/creative/test_copy_guard.py::test_alcohol_words_only_blocked_when_a_minor_is_likely` |
| A minor is inferred from objects (a stroller), not from a face | Vote over detected objects | `edge/tests/test_vocabulary.py::test_a_stroller_next_to_a_person_flags_minor_after_the_vote` |

### Speech

| Concern | Enforced by | Test |
|---|---|---|
| Speech is off unless the screen opted in | `--speech-mode` flag; without it the process exits ([`edge/agent/speech/__main__.py`](../edge/agent/speech/__main__.py)). The launcher refuses speech without paid providers opted in | `tests/unit/api/test_demo_provider_isolation.py::test_speech_without_paid_opt_in_exits_before_starting_processes` |
| A visible indicator while the mic is open | "Micrófono activo · solo temas, nada se graba" on the TV ([`ScreenPage.tsx`](../web/src/features/screen/ScreenPage.tsx)) | `web/src/features/screen/ScreenPage.test.tsx` |
| Transcripts are never logged | The pipeline logs counts only | `edge/tests/test_speech_pipeline.py::test_utterance_text_never_reaches_the_logs` |
| Transcripts are never stored | `AudienceUtterance.text` is "ephemeral: never persisted or logged" ([`messages.py`](../app/contract/messages.py)); no migration has a column for it | Schema review: `db/migrations/` has `auction`, `charge`, `campaign`, `redemption`, `outbox_event`, `basket_offer` (approved offer facts, "never sensor input") and `playback_receipt`, nothing else |
| Spoken topics expire | TTL on topics | `tests/unit/api/test_speech_and_voice_flow.py::test_speech_expires_and_the_screen_reverts` |
| Muting the mic forgets at once | The live switch | `tests/unit/api/test_live_switch_flow.py::test_mic_off_drops_utterances_but_keeps_the_camera` |

### Nothing is sensed until a person turns it on

| Concern | Enforced by | Test |
|---|---|---|
| Standby means no sensing, enforced twice | Agents pause themselves (`--await-live`), **and** the backend drops sensed data at the socket ([`app/realtime/live_switch.py`](../app/realtime/live_switch.py)) | `test_live_switch_flow.py::test_standby_drops_sensing_and_going_live_resumes_the_loop`, `tests/unit/api/test_live_demo_supervisor.py::test_the_station_starts_in_standby_unless_asked_to_start_live` |

### What we store

| Concern | Enforced by | Test |
|---|---|---|
| Stored audience data is aggregate | The `auction` row keeps `people`, `looking`, `minor_likely`, `coverage` and bids per moment, with no per-person row ([`0002_auctions_and_charges.sql`](../db/migrations/0002_auctions_and_charges.sql)) | `tests/unit/architecture/test_domain_pattern_guardrails.py` (append-only tables) |
| Redemptions are anonymous | Code + `sha256(nonce)` only, unique per (code, nonce) ([`redemption.py`](../app/modules/delivery/domain/redemption.py), [`0004_redemptions.sql`](../db/migrations/0004_redemptions.sql)). The offer page says "Sin cuenta y sin datos personales" ([`redeem.py`](../app/api/redeem.py)) | `tests/unit/api/test_redeem_page.py` |
| The console never shows camera pixels | "Lo que ve la cámara" draws boxes only ([`station-d.tsx`](../web/src/features/console/station-d.tsx)) | n/a (UI) |

### What the screen discloses

| Concern | Enforced by | Test |
|---|---|---|
| People can see the camera is on | "Cámara activa · grupos anónimos, nada se graba" on the TV whenever the live switch has sensing on ([`ScreenPage.tsx`](../web/src/features/screen/ScreenPage.tsx), `data-camera`) | `web/src/features/screen/ScreenPage.test.tsx` ("shows the camera chip whenever the live switch has sensing on") |

### Who can connect

| Concern | Enforced by | Test |
|---|---|---|
| A stranger can't feed or read the sensor stream | `AGENT_TOKEN` / `SCREEN_TOKEN` are required in prod (24+ chars) ([`device_auth.py`](../app/api/device_auth.py)) | `tests/unit/api/test_ws_adversarial.py` |
| The `/dev` stream (the only one that can carry a redacted keyframe preview) is never in prod | `dev_console_enabled`, `dev_keyframe_preview` is opt-in ([`settings.py`](../app/config/settings.py)) | `tests/unit/realtime/test_dev_stream.py` |
| Demo controls are hidden | `DEMO_KEY`, 404 without it ([`demo_access.py`](../app/api/demo_access.py)) | `tests/unit/api/test_demo_access.py` |

## 3. Open gaps (say these before a judge finds them)

| # | Gap | Risk | Fix |
|---|---|---|---|
| G5 | **Third parties process speech.** ElevenLabs gets the audio; Anthropic gets the text the keyterm matcher missed | Must be disclosed. "Never stored" holds for *our* systems; provider retention depends on their terms | Confirm zero-retention settings with both providers; state it in the signage |
| G6 | The objects-only rule for the VLM lives in a prompt. Only the schema is enforced (no person fields) | A free-text `evidence` string could still describe a person | Run the evidence through the same guard, or drop free text |
| G7 | Analysing people with a camera is **personal data processing** under Ley 21.719 and the GDPR, even without identification | Legal | Signage, a documented legal basis, a DPIA and legal review before a real venue (product §7 already says so) |
| G8 | Frames leave the store for our GPU service | "Never stored" is a promise plus one test, not an attestation | A venue LAN GPU box (an ADR 0007 option) keeps frames on site |
| G9 | Residual of the G1 fix: a person the hot path missed stands **inside** a kept region (behind a detected person, or holding a detected object). A second, high-recall pose pass (`HighRecallPeople`) runs on every keyframe and blacks out the heads it finds, so the residual is a face *both* passes missed inside a kept region | Low: needs two misses *and* an overlap | Only a face model could close it fully, and the stack rules one out; the kept regions stay tight (object padding 10 %) |
| G10 | The console's camera preview (demo only, first party) is the whole frame with detected heads blacked out, so a missed face shows there | First party, in memory, never stored; never sent to a third party | Leave the preview off (`EDGE_CAMERA_PREVIEW` unset) outside the demo |

**Fixed on 2026-11-22:**

| # | Was | Now |
|---|---|---|
| G1 | The scene keyframe blurred only heads YOLO detected; a missed face went to Claude | Allow-list redaction: only detected people and objects are copied onto black |
| G2 | The backend trusted the agent's blur | The backend checks the pixels against the declared regions, fail closed |
| G3 | Gaussian blur, which is reversible in principle | Opaque black everywhere (keyframe and preview) |
| G4 | No camera indicator on the TV | "Cámara activa" chip whenever sensing is on |

## 4. Answers for judges (backed by §2)

- **"Is this facial recognition?"** No. There is no face model and no embeddings, and track ids are a per-session counter. The AI only ever sees what the detector vouched for (people and products), with every head blacked out; everything else, including someone the detector missed, is black. The backend checks that redaction and refuses any frame that fails it.
- **"Can the AI guess my race, age or mood?"** The model's output schema has no field for any of it: segment ids are a closed list of interests. There's a test for that.
- **"Do you record?"** No. The GPU service has a test that patches every file write and asserts none happen. The database holds per-moment counts, prices and hashed redemptions, nothing else.
- **"Are you listening?"** Only on an opted-in screen, with "Micrófono activo" on the TV. Transcripts are never logged (tested) or stored; topics expire.
- **"How would I know it's watching?"** The TV says "Cámara activa · grupos anónimos, nada se graba" whenever it senses, and "Micrófono activo" when the mic is open.
- **What's still open:** G5–G10 above. The biggest is legal (G7): signage and a DPIA before a real venue.

## Station restart and uncertain safety (2026-11-22)

`MinorLikely.uncertain` records only that available pose geometry cannot support a
clear safety reading (cropped/occluded bodies or an unsettled vote). It is separate
from `value`: an unknown audience is **not** a detected child. Both conditions block
age-restricted categories in `AudienceClassifier`; neither supplies a targeting
segment or changes another bid. The snapshot publishes a new block immediately and
requires stability to clear it. The console labels uncertainty explicitly. The
pose detector can still miss children; the conservative block closes the unsafe
interpretation of missing evidence. Adult-only views may also be blocked.

Evidence: `edge/tests/test_minor.py`,
`tests/unit/audience/test_classifier.py::test_uncertain_camera_safety_only_blocks_and_never_changes_target_segments`,
`evals/scenarios/13_uncertain_safety.json`, and the labelled real-video protection
gate in `evals/video/minors.py`. No new model, facial recognition, demographic
attribute, or per-person stored evidence was introduced.

The local station now starts and migrates Postgres. `basket_offer` stores approved
basket/QR facts, and `playback_receipt` stores aggregate counts, attention, monetary
charges and filtering totals. They contain no frames, audio, utterances, tracklet
ids or embeddings. They are tenant-filtered, immutable append-only tables with
DB triggers and architecture guardrails. Only nonce hashes remain in redemptions.
Offer issuance commits before publishing the QR; receipt charges and receipt JSON
commit in one transaction before the console acknowledgment. Redemptions commit
before acknowledgment. Startup restores offers, redemption dedupe/caps, and the
last receipt per screen. Anonymous live tracking and attention samples remain
session-local and are never restored across a restart.
