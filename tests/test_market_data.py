from datetime import date, datetime

import pandas as pd

from rsi2_bot.market_data import NY_TZ, bars_since, completed_bars, is_fresh, session_of

SESSIONS = [date(2026, 9, d) for d in (21, 22, 23, 24, 25)]  # lundi -> vendredi


def ms(year, month, day, hour, minute=0):
    return int(datetime(year, month, day, hour, minute, tzinfo=NY_TZ).timestamp() * 1000)


def closes():
    return pd.Series([1.0, 2.0, 3.0, 4.0, 5.0], index=SESSIONS)


def test_completed_bars_drops_today_before_close():
    now = datetime(2026, 9, 25, 15, 59, tzinfo=NY_TZ)
    assert completed_bars(closes(), now).index[-1] == date(2026, 9, 24)


def test_completed_bars_keeps_today_after_close():
    now = datetime(2026, 9, 25, 16, 15, tzinfo=NY_TZ)
    assert completed_bars(closes(), now).index[-1] == date(2026, 9, 25)


def test_is_fresh():
    assert is_fresh(closes(), datetime(2026, 9, 25, 16, 15, tzinfo=NY_TZ))
    assert not is_fresh(closes(), datetime(2026, 9, 28, 16, 15, tzinfo=NY_TZ))  # lundi, données pas à jour


def test_session_of_after_close_fill_belongs_to_same_day():
    # Bot lancé à 22h15 Paris = 16h15 New York
    assert session_of(ms(2026, 9, 23, 16, 15), SESSIONS) == date(2026, 9, 23)


def test_session_of_weekend_fill_belongs_to_friday():
    assert session_of(ms(2026, 9, 26, 12), SESSIONS) == date(2026, 9, 25)


def test_session_of_before_history_is_none():
    assert session_of(ms(2026, 9, 18, 16), SESSIONS) is None


def test_bars_since():
    assert bars_since(date(2026, 9, 25), SESSIONS) == 0
    assert bars_since(date(2026, 9, 22), SESSIONS) == 3
    assert bars_since(date(2026, 9, 21), SESSIONS) == 4


def test_paris_2215_is_after_us_close_all_year():
    """22h15 Paris tombe toujours après 16h00 New York, y compris pendant les
    semaines de décalage des changements d'heure (mars et octobre/novembre)."""
    from zoneinfo import ZoneInfo

    paris = ZoneInfo("Europe/Paris")
    for day in pd.date_range("2026-01-01", "2026-12-31", freq="D"):
        t = datetime(day.year, day.month, day.day, 22, 15, tzinfo=paris).astimezone(NY_TZ)
        assert t.date() == day.date()
        assert t.hour >= 16
