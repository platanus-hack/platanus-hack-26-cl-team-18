# Documentation index

Pick a reading path by role, then use the tables to look things up. Each doc
states its own status at the top. **LIVE** docs track the code, **BINDING**
docs change only through a new ADR, and **PLAN** docs record how a feature
was built (a later doc or the code wins where they disagree).

## Reading paths

| You are… | Read, in order |
|---|---|
| **New to the project** (judge, reviewer, new teammate) | [`../README.md`](../README.md) → [product](product.md) → [architecture](architecture.md) §1–3 → [judge Q&A](judge-qa.md) |
| **Building a feature** | [product](product.md) → [stack](stack.md) → [architecture](architecture.md) → [rules](rules.md) → [common patterns](common_patterns.md) → [`../CONTEXT.md`](../CONTEXT.md) |
| **Running the demo** | [demo](demo.md) → [simulator](simulator.md) → [demo video](demo-video.md) → [judge Q&A](judge-qa.md) |
| **Touching perception, speech or storage** | [privacy](privacy.md) → [ADR 0004](adr/0004-python-perception-agent-yolo.md), [0005](adr/0005-elevenlabs-speech-and-voice.md), [0007](adr/0007-remote-gpu-perception.md), [0010](adr/0010-screen-page-senses.md) |
| **Deploying** | [terraform](terraform.md) → [`../terraform/README.md`](../terraform/README.md) |

## Product and demo

| Doc | Status | What it answers |
|---|---|---|
| [product.md](product.md) | LIVE | The problem, the loop, product invariants, metrics and scope. Source of truth for *what* and *why*. |
| [demo.md](demo.md) | LIVE | Station setup, console layout, the five-beat script, demo insurance, pitch |
| [demo-video.md](demo-video.md) | LIVE | Shot list for the recorded demo, proven by `make eval-demo-video` |
| [judge-qa.md](judge-qa.md) | LIVE | Likely judge questions, the answer, the file that proves it, and what not to claim |

## Architecture and engineering

| Doc | Status | What it answers |
|---|---|---|
| [architecture.md](architecture.md) | LIVE | Runtime topology, latency budget, bounded contexts, the VCG bundle auction, voice, fast video, failure modes, message contract |
| [stack.md](stack.md) | BINDING (ADR 0006) | Devices and their settings, every component, what we don't use, the day-1 benchmark gate |
| [rules.md](rules.md) | LIVE GUIDE | Architecture rules: layering, aggregates, events, security, queue consumers |
| [common_patterns.md](common_patterns.md) | LIVE | How to use the shared kernel: value objects, message bus, unit of work, outbox |
| [folder_structure.md](folder_structure.md) | LIVE | Repo layout and the anatomy of a bounded context |
| [`../CONTEXT-MAP.md`](../CONTEXT-MAP.md) | LIVE | How the six bounded contexts talk |
| [`../CONTEXT.md`](../CONTEXT.md) | LIVE | Domain glossary: moment, segment, bundle, attention-second… |
| [adr/](adr/README.md) | BINDING | Architecture decision records |

## Feature deep dives

| Doc | Status | What it answers |
|---|---|---|
| [creative-generation.md](creative-generation.md) | LIVE | Brief → angle and group offer → stock or AI copy → guard → score; `make eval-creative` |
| [latency-feedback-loop.md](latency-feedback-loop.md) | LIVE (measurements) | Sensing-to-ad latency hypotheses, controlled trials and results |
| [simulator.md](simulator.md) | LIVE | Run the audience replay without a camera |
| [auction-plan.md](auction-plan.md) | PLAN, partly implemented | Build plan for `app/modules/auction`; §10 lists what landed |
| [dev-console-plan.md](dev-console-plan.md) | PLAN, implemented | `/dev`, the QA and developer console |

## Quality, privacy, operations

| Doc | Status | What it answers |
|---|---|---|
| [evals.md](evals.md) | LIVE | Product evals: gates vs ratchets, the scorecard, real-video perception, rehearsal scoring |
| [privacy.md](privacy.md) | LIVE | Each privacy concern → the code that enforces it → its test, plus open gaps |
| [terraform.md](terraform.md) | LIVE | Render and Terraform rules, deploy runbook |

## Component READMEs

| Component | README |
|---|---|
| Perception agent (`edge/`) | [`../edge/README.md`](../edge/README.md) |
| GPU inference service (`gpu/`) | [`../gpu/README.md`](../gpu/README.md) |
| Player and console (`web/`) | [`../web/README.md`](../web/README.md) |
| Shared kernel | [`../app/modules/common/README.md`](../app/modules/common/README.md) |
| LLM kernel | [`../app/modules/ai/README.md`](../app/modules/ai/README.md) |
| Bounded contexts | `../app/modules/<context>/CONTEXT.md` (linked from [`../CONTEXT-MAP.md`](../CONTEXT-MAP.md)) |

## Working with agents

[`../CLAUDE.md`](../CLAUDE.md) (symlinked as `AGENTS.md`) is the canonical
instruction file for coding agents. Issue-tracker, triage-label and
domain-language conventions for agent skills live in [`agents/`](agents/).
