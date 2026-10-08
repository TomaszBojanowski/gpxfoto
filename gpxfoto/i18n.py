"""Translations and regional settings."""
import gettext
import locale
import os
from gettext import gettext as _

DOMAIN = "gpxfoto"
# realpath: in a strict editable install this file is a symbolic link
LOCALE_DIR = os.path.join(os.path.dirname(os.path.realpath(__file__)), "locale")


def setup():
    """Use the system's regional settings and the program's translations.

    The domain is set globally, so the messages printed by argparse are
    translated together with the program's own.
    """
    try:
        locale.setlocale(locale.LC_ALL, "")
    except locale.Error:
        pass
    gettext.bindtextdomain(DOMAIN, LOCALE_DIR)
    gettext.textdomain(DOMAIN)


def catalog():
    """The translations for the page of the browser interface.

    Returns {"language", "locale", "messages", "plural"}: messages maps
    each msgid to its translation, or the msgid of a message with plural
    forms to the list of its forms; plural gives the index of the form
    for counts 0 to 199, and for a larger count n, plural[100 + n % 100].
    locale is a language tag for formatting numbers, such as "pl-PL".
    """
    translation = gettext.translation(DOMAIN, LOCALE_DIR, fallback=True)
    messages = {}
    for key, text in getattr(translation, "_catalog", {}).items():
        if isinstance(key, tuple):
            msgid, index = key
            forms = messages.setdefault(msgid, [])
            forms.extend([""] * (index + 1 - len(forms)))
            forms[index] = text
        elif key:
            messages[key] = text
    plural = getattr(translation, "plural", lambda n: int(n != 1))
    language = (translation.info().get("language") or "en") if messages else "en"
    name = locale.getlocale(locale.LC_NUMERIC)[0] or ""
    return {"language": language, "locale": name.replace("_", "-") or language,
            "messages": messages, "plural": [plural(n) for n in range(200)]}


def number(value, decimals=0, width=0):
    """Format a number according to the regional settings."""
    return locale.format_string(f"%.{decimals}f", value, grouping=True).rjust(width)


def coordinates(lat, lon):
    """Format latitude and longitude according to the regional settings.

    With a decimal comma, a comma between the two numbers would be
    ambiguous, so they are separated by a semicolon.
    """
    separator = "; " if locale.localeconv()["decimal_point"] == "," else ", "
    return number(lat, 6) + separator + number(lon, 6)


def distance(metres):
    """A distance in metres or, from 1 km, in kilometres, e.g. “350 m” or “1.8 km”."""
    if round(metres) < 1000:
        # Translators: a distance in metres
        return _("{metres} m").format(metres=number(metres))
    # Translators: a distance in kilometres
    return _("{kilometres} km").format(kilometres=number(metres / 1000, 1))


def duration(seconds):
    """A time span in whole seconds, minutes or hours and minutes, e.g. “2 min”."""
    s = int(round(seconds))
    if s < 120:
        return _("{seconds} s").format(seconds=s)
    if s < 7200:
        return _("{minutes} min").format(minutes=s // 60)
    return _("{hours} h {minutes} min").format(hours=s // 3600, minutes=s % 3600 // 60)


def exact_duration(seconds, sign=False):
    """A time span to the millisecond, e.g. “1 h 0 min 5 s” or, with sign, “+2 min 11.52 s”.

    Parts that are zero at the end are left out, so 3600 s is “1 h”. The
    numbers follow the regional settings.
    """
    milliseconds = round(abs(seconds) * 1000)
    hours, rest = divmod(milliseconds, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    if rest % 1000:
        point = locale.localeconv()["decimal_point"]
        second_text = number(rest / 1000, 3).rstrip("0").rstrip(point)
    else:
        second_text = number(rest // 1000)
    if hours and rest:
        text = _("{hours} h {minutes} min {seconds} s").format(
            hours=number(hours), minutes=number(minutes), seconds=second_text)
    elif hours and minutes:
        text = _("{hours} h {minutes} min").format(hours=number(hours), minutes=number(minutes))
    elif hours:
        text = _("{hours} h").format(hours=number(hours))
    elif minutes and rest:
        text = _("{minutes} min {seconds} s").format(minutes=number(minutes), seconds=second_text)
    elif minutes:
        text = _("{minutes} min").format(minutes=number(minutes))
    else:
        text = _("{seconds} s").format(seconds=second_text)
    if sign and milliseconds:
        text = ("+" if seconds > 0 else "-") + text
    return text


def N_(message):
    """Mark a string for translation without translating it yet."""
    return message
