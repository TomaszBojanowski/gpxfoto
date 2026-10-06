"""Translations and regional settings."""
import gettext
import locale
import os

DOMAIN = "gpxfoto"
LOCALE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "locale")


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


def number(value, decimals=0, width=0):
    """Format a number according to the regional settings."""
    return locale.format_string(f"%.{decimals}f", value, grouping=True).rjust(width)


def N_(message):
    """Mark a string for translation without translating it yet."""
    return message
