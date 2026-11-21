from app.modules.common.domain.value_objects.timezone import TimeZone


def test_timezone_accepts_panama_iana_name() -> None:
    timezone = TimeZone.create("America/Panama")

    assert str(timezone) == "America/Panama"
