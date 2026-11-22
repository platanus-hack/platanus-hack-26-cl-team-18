"""Claude Haiku 5.5 copywriter (warm path) behind the `CopyWriter` port.

Calls the `ai` module's `ILLMProvider` directly (not `AIService`) so provider
failures stay typed and map to `CopyFailure` here; raw provider text never
leaves this adapter. The system prompt is frozen (cacheable); the per-moment
brief goes in the user turn as JSON. The brief carries no personal data and no
utterance text. Output is constrained to `_CopyOut` (structured output) and is
still guarded and scored by the studio before it can air.
"""

from __future__ import annotations

import json
from decimal import Decimal

from pydantic import BaseModel

from app.modules.ai.domain.llm.provider import InferenceParams
from app.modules.ai.domain.llm.service import build_message
from app.modules.ai.domain.model_capabilities import ModelProfile
from app.modules.ai.infrastructure.parsers.pydantic_json import (
    parse_pydantic_json,
    structured_output_for,
)
from app.modules.ai.ports.errors import (
    ProviderCreditExhausted,
    ProviderOutputIncomplete,
    ProviderRateLimited,
    ProviderRequestRejected,
    ProviderUnavailable,
)
from app.modules.ai.ports.provider import ILLMProvider
from app.modules.creative.application.ports.copywriter import (
    CopyFailure,
    CopyWriterError,
    TokenUsage,
    WrittenCopy,
)
from app.modules.creative.application.stock_copy import StockCopywriter
from app.modules.creative.domain.brief import CreativeBrief
from app.modules.creative.domain.cost import estimate_tokens
from app.modules.creative.domain.draft import CopyDraft, CopySource
from app.modules.creative.domain.lexicon import ANGLE_KEYWORDS

SYSTEM_PROMPT = """\
You write the copy for one ad on a digital screen above the counter of a convenience \
store or gas-station shop in Chile. A small group is looking at the screen from 2 to \
4 meters away. Brands that sell well together share the screen as a basket: one joint \
offer sized to the group. The screen shows the brands' real logos; you write only text.

You receive a JSON brief and return four lines in Chilean Spanish:
- teaser: the hook, shown first (0-3 s). It answers the moment's angle in a few words.
- offer: the joint offer (3-8 s). It must contain the offer price exactly as given.
- close: the call to action (8 s+), pointing at the QR code; the code is valid 10 minutes.
- voice_line: one sentence a narrator says aloud, natural to speak, no symbols.

Hard rules (copy that breaks one never airs):
1. Prices: write the offer price exactly as `offer.price`, in digits, in every line \
that mentions it (the voice line too: "$8.990", never "ocho mil"). Never write any other price, \
percentage, discount, "gratis" or "regalo" unless it is in the offer or `claims`.
2. Brands: name every brand in `brands` across teaser, offer and close; name them all in \
the offer line when you can. Never name anything in `avoid`.
3. Group: if `address_group_as` is set, address the group with exactly those words \
("para los 4"). Never state any other number of people. If `offer_for_fewer` is true, \
the offer serves fewer people than are looking: offer it "para compartir" and never \
say it is for everyone ("para todo el grupo", "para todos", "cada uno").
4. Claims: only the claims in `claims`. No health, medical or "the best" claims.
5. People: never describe or guess anything about the viewers (looks, age, body, \
religion, politics, health, origin) and never say you see, watch or recognize them.
6. If `minor_likely` is true, no alcohol, betting or drinking words.
7. No links, handles, phone numbers or emojis.
8. Stay within `limits` (words per line).

House style (write like a senior copywriter at a top retail agency, not like a chatbot):
- Statements, not questions. No "¿Hambre?" openers, no exclamation marks, no ellipses, \
no em dashes, no emojis.
- Concrete over clever: name the product, the hour, the action. One idea per line.
- Short, rhythmic sentences. Two beats work: "Entrenaste. Ahora recarga." \
"Pausa corta. Café largo."
- Chilean Spanish, used lightly ("pilas bajas", "bajón", "al paso"). At most one \
idiom per ad; never "al tiro" in every line.
- Banned filler (copy that uses it never airs): momento perfecto, complemento perfecto, \
experiencia, descubre, eleva, irresistible, imperdible, increíble, espectacular, \
siguiente nivel, disfruta, date un gusto, te lo mereces, hecho para ti, tu momento.
- teaser: at most 6 words, makes the angle obvious (`angle_words` lists words viewers \
recognize for it). Use `daypart` or `weather` (hot, cold, rain) when it sharpens the \
hook; never claim a product fixes the weather.
- offer: copy `offer.text` character for character (its quantities and items are the \
brands' claims: "Voltio + Café Patio x2" never becomes "dos Café Patio"), add the group, \
and end the line with the price ("Voltio + Café Patio x3 para los 3 por $8.490."). The \
screen sets the final price apart in large type. If `offer` is null, build the line from \
`approved_lines` and never mention a price or an offer.
- close: one instruction. Scan, then redeem at the counter ("canjea en caja").
- Brand voice: write in the lead brand's `voice` (the first brand); partners keep \
their names, not their tone. A brand's `tagline` is its own approved line: use it \
verbatim or not at all, never paraphrased.
`baseline` is the current stock copy: beat it, keep its facts.
Also return `confidence` (0 to 1) that the copy follows every rule, and `reasoning`: \
one short sentence on the choice.\
"""


class _CopyOut(BaseModel):
    teaser: str
    offer: str
    close: str
    voice_line: str
    confidence: float
    reasoning: str


def brief_payload(brief: CreativeBrief) -> dict[str, object]:
    baseline = StockCopywriter().compose(brief)
    offer = brief.offer
    return {
        "brands": [
            {"name": b.name, "category": b.category, "voice": list(b.voice), "tagline": b.tagline}
            for b in brief.brands
        ],
        "party": brief.party,
        "address_group_as": (
            f"para los {brief.addressed_party}"
            if brief.addressed_party
            else None
            if brief.offer_undersized or not brief.is_group
            else "para todo el grupo"
        ),
        "offer_for_fewer": brief.offer_undersized,
        "people": brief.people,
        "angle": brief.angle.angle.value,
        "angle_reason": brief.angle.reason.value,
        "angle_words": list(ANGLE_KEYWORDS.get(brief.angle.angle, ())),
        "held_product_brand": brief.held_brand,
        "offer": (
            {
                "label": offer.label,
                "text": offer.text,
                "price": offer.price_text,
                "for_party": offer.min_party,
            }
            if offer
            else None
        ),
        "claims": list(offer.claims) if offer else [],
        "approved_lines": [b.stock_copy for b in brief.brands],
        "avoid": sorted(brief.avoid_names),
        "minor_likely": brief.minor_likely,
        "daypart": brief.daypart.value if brief.daypart else None,
        "weather": brief.weather.value if brief.weather else None,
        "limits": {
            "teaser_words": brief.limits.teaser_words,
            "offer_words": brief.limits.offer_words,
            "close_words": brief.limits.close_words,
            "voice_words": brief.limits.voice_words,
        },
        "baseline": {
            "teaser": baseline.teaser,
            "offer": baseline.offer,
            "close": baseline.close,
        },
    }


class ClaudeCopywriter:
    name = "claude"

    def __init__(
        # Thinking counts against max_tokens: at 1024 some briefs stopped mid-answer
        # (max_tokens -> invalid output). The answer itself is ~100 tokens.
        self,
        provider: ILLMProvider,
        profile: ModelProfile,
        *,
        max_tokens: int = 2048,
    ) -> None:
        self._provider = provider
        self._profile = profile
        self._params = InferenceParams.create(
            max_tokens=max_tokens, structured_output=structured_output_for(_CopyOut)
        )

    async def write(self, brief: CreativeBrief) -> WrittenCopy:
        user = json.dumps(brief_payload(brief), ensure_ascii=False, sort_keys=True)
        messages = build_message(system_prompt=SYSTEM_PROMPT, user_prompt=user)
        try:
            response = await self._provider.complete(messages, self._params)
        except ProviderCreditExhausted as exc:
            raise CopyWriterError(CopyFailure.QUOTA) from exc
        except ProviderRateLimited as exc:
            raise CopyWriterError(CopyFailure.RATE_LIMITED) from exc
        except ProviderUnavailable as exc:
            raise CopyWriterError(CopyFailure.UNAVAILABLE) from exc
        except ProviderRequestRejected as exc:
            raise CopyWriterError(CopyFailure.REFUSED) from exc
        except ProviderOutputIncomplete as exc:
            raise CopyWriterError(CopyFailure.INVALID_OUTPUT) from exc
        except Exception as exc:
            raise CopyWriterError(CopyFailure.UNAVAILABLE) from exc
        try:
            out = parse_pydantic_json(response.output, _CopyOut)
        except Exception as exc:
            raise CopyWriterError(CopyFailure.INVALID_OUTPUT) from exc
        if not isinstance(out, _CopyOut):
            raise CopyWriterError(CopyFailure.INVALID_OUTPUT)
        confidence = Decimal(str(min(max(out.confidence, 0.0), 1.0))).quantize(Decimal("0.01"))
        draft = CopyDraft(
            teaser=out.teaser.strip(),
            offer=out.offer.strip(),
            close=out.close.strip(),
            voice_line=out.voice_line.strip(),
            source=CopySource.AI,
            model_version=self._profile.describe(),
            confidence=confidence,
            reasoning=out.reasoning.strip()[:300],
        )
        return WrittenCopy(
            draft=draft,
            usage=TokenUsage(
                input_tokens=estimate_tokens(SYSTEM_PROMPT + user),
                output_tokens=estimate_tokens(response.output),
                estimated=True,
            ),
        )
