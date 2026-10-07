"""Time spans formatted for people (gpxfoto.i18n)."""
import locale

import pytest

from gpxfoto import i18n


@pytest.fixture(autouse=True)
def english(monkeypatch):
    # Texts are compared exactly; make sure no catalogue translates them.
    monkeypatch.setenv("LANGUAGE", "C")


@pytest.fixture
def numeric_locale():
    """Set LC_NUMERIC for one test; the test is skipped if it is not installed."""
    saved = locale.setlocale(locale.LC_NUMERIC)

    def use(name):
        try:
            locale.setlocale(locale.LC_NUMERIC, name)
        except locale.Error:
            pytest.skip(f"the {name} locale is not installed")

    yield use
    locale.setlocale(locale.LC_NUMERIC, saved)


@pytest.mark.parametrize("seconds, text", [
    (0, "0 s"),
    (0.4, "0 s"),
    (59, "59 s"),
    (119, "119 s"),
    (119.4, "119 s"),
    (119.6, "2 min"),
    (120, "2 min"),
    (179, "2 min"),
    (180, "3 min"),
    (3600, "60 min"),
    (7199, "119 min"),
    (7199.4, "119 min"),
    (7199.6, "2 h 0 min"),
    (7200, "2 h 0 min"),
    (7259, "2 h 0 min"),
    (7260, "2 h 1 min"),
    (10799, "2 h 59 min"),
    (90061, "25 h 1 min"),
])
def test_duration(seconds, text):
    assert i18n.duration(seconds) == text


@pytest.mark.parametrize("seconds, sign, text", [
    (0, False, "0 s"),
    (0, True, "0 s"),
    (0.0004, True, "0 s"),
    (0.001, False, "0.001 s"),
    (0.5, False, "0.5 s"),
    (12.3456, False, "12.346 s"),
    (59.9996, False, "1 min"),
    (132, False, "2 min 12 s"),
    (132, True, "+2 min 12 s"),
    (-3468, True, "-57 min 48 s"),
    (-3468, False, "57 min 48 s"),
    (131.52, True, "+2 min 11.52 s"),
    (1800, True, "+30 min"),
    (3600, False, "1 h"),
    (-3600, True, "-1 h"),
    (3605, False, "1 h 0 min 5 s"),
    (3660, False, "1 h 1 min"),
    (3732, True, "+1 h 2 min 12 s"),
    (1234 * 3600 + 1, True, "+1234 h 0 min 1 s"),
])
def test_exact_duration(seconds, sign, text):
    assert i18n.exact_duration(seconds, sign) == text


@pytest.mark.parametrize("seconds, text", [
    (131.52, "+2 min 11,52 s"),
    (1234 * 3600 + 1, "+1 234 h 0 min 1 s"),
    (0.5, "+0,5 s"),
])
def test_exact_duration_follows_the_regional_settings(numeric_locale, seconds, text):
    numeric_locale("pl_PL.UTF-8")
    assert i18n.exact_duration(seconds, sign=True) == text
