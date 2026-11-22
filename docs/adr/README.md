# Architecture decision records

Each ADR records one binding decision: its context, the decision and its
consequences. To change a binding decision, write a new ADR that supersedes
or amends the old one. Don't rewrite history in place; add an "Amendment"
section or a new record.

| ADR | Decision | Status |
|---|---|---|
| [0001](0001-three-speed-latency-paths.md) | Three-speed latency paths: hot, warm and cold. Hot never awaits a network model. | Accepted |
| [0002](0002-browser-is-the-device.md) | The browser is the device (in-browser perception with MediaPipe) | Superseded by 0004; the browser stays the player |
| [0003](0003-no-generative-video-in-the-loop.md) | No generative video in the real-time loop: pre-generated bundle loops, warm turbo upgrades under a budget cap | Accepted, amended |
| [0004](0004-python-perception-agent-yolo.md) | Python perception agent with YOLO | Accepted, amended by 0010 |
| [0005](0005-elevenlabs-speech-and-voice.md) | ElevenLabs for speech recognition (Scribe) and voice-overs (Flash) | Accepted, amended by 0010 |
| [0006](0006-committed-latency-first-stack.md) | The committed latency-first stack; swapping a component needs a new ADR | Accepted |
| [0007](0007-remote-gpu-perception.md) | YOLO on a remote stateless GPU service, with a hot-standby local detector | Accepted |
| [0008](0008-demo-critical-decisions.md) | Demo-critical decisions: vision first, prop markers, tunnel, at most 2 brands, coverage headline, CLP/Spanish, two laptops | Accepted |
| [0009](0009-station-readiness-and-uncertain-safety.md) | Persistent station records, reachable QR codes, conservative safety when uncertain | Accepted |
| [0010](0010-screen-page-senses.md) | The `/screen` page can be its own sensor: in-browser pose and Scribe mic | Accepted |

## Template

```markdown
# NNNN: <decision in one line>

- Status: proposed | accepted | superseded by [NNNN](…) | amended YYYY-MM-DD
- Date: YYYY-MM-DD
- Amends / Supersedes: ADR NNNN (optional)

## Context
What forces the decision: constraints, measurements, what we tried.

## Decision
What we will do, stated so it can be checked in review.

## Consequences
What gets easier, what gets harder, and what to watch.
```
