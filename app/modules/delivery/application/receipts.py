"""Present a measured play without persisting any sensor samples or tracklets."""

from collections.abc import Mapping

from app.contract.messages import (
    ChargeLine,
    ChargeReason,
    ConsoleReceipt,
    FilteredAttention,
    MoneyDTO,
)
from app.modules.campaigns.domain.book import BrandId
from app.modules.delivery.application.billing import Receipt


def receipt_message(receipt: Receipt, names: Mapping[BrandId, str]) -> ConsoleReceipt:
    return ConsoleReceipt(
        screen_id=receipt.play.screen_id,
        creative_id=receipt.play.creative_id,
        auction_id=receipt.play.auction_id,
        people=receipt.play.people,
        looking=receipt.play.looking,
        played_s=format(receipt.played_s, "f"),
        attention_seconds=format(receipt.attention.attention_seconds, "f"),
        charges=[
            ChargeLine(
                campaign_id=str(c.campaign_id),
                brand_name=names[c.brand_id],
                attention_seconds=format(c.attention_seconds, "f"),
                price_per_as=MoneyDTO(
                    amount=format(c.price_per_as.amount.normalize(), "f"),
                    currency=str(c.price_per_as.currency),
                ),
                amount=MoneyDTO(
                    amount=format(c.amount.amount, "f"), currency=str(c.amount.currency)
                ),
                reason=ChargeReason(c.reason.value),
            )
            for c in receipt.charges
        ],
        total=MoneyDTO(
            amount=format(receipt.total.amount, "f"), currency=str(receipt.total.currency)
        ),
        filtered=[
            FilteredAttention(reason=r.value, seconds=format(s, "f"))
            for r, s in receipt.attention.invalid_seconds.items()
            if s > 0
        ],
    )
