from __future__ import annotations

from enum import StrEnum


class TimeZoneEnum(StrEnum):
    UTC = "UTC"

    # Americas
    AMERICA_SANTIAGO = "America/Santiago"
    AMERICA_SAO_PAULO = "America/Sao_Paulo"
    AMERICA_BUENOS_AIRES = "America/Argentina/Buenos_Aires"
    AMERICA_LIMA = "America/Lima"
    AMERICA_BOGOTA = "America/Bogota"
    AMERICA_COSTA_RICA = "America/Costa_Rica"
    AMERICA_GUAYAQUIL = "America/Guayaquil"
    AMERICA_PANAMA = "America/Panama"
    AMERICA_MEXICO_CITY = "America/Mexico_City"
    AMERICA_MONTEVIDEO = "America/Montevideo"
    AMERICA_NEW_YORK = "America/New_York"
    AMERICA_LOS_ANGELES = "America/Los_Angeles"

    # Europe
    EUROPE_LONDON = "Europe/London"
    EUROPE_MADRID = "Europe/Madrid"
    EUROPE_PARIS = "Europe/Paris"
    EUROPE_BERLIN = "Europe/Berlin"

    # Asia
    ASIA_TOKYO = "Asia/Tokyo"
    ASIA_JERUSALEM = "Asia/Jerusalem"
    ASIA_SHANGHAI = "Asia/Shanghai"
    ASIA_SINGAPORE = "Asia/Singapore"
    ASIA_YEREVAN = "Asia/Yerevan"

    # Oceania
    AUSTRALIA_SYDNEY = "Australia/Sydney"
