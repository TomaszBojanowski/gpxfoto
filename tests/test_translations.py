"""Translation catalogs (po/) and the Polish output of the command-line tool."""
import ast
import gettext
import locale
import os
import re
import shutil
import subprocess
import sys
import time
import types

import pytest

from conftest import (
    ROOT, hike_gpx, make_jpeg, needs_exiftool, set_panasonic_time_stamp, set_tags, write_gpx)
from gpxfoto import cli, i18n
from gpxfoto.engine.checks import ShiftHint
from gpxfoto.server import warnings
from test_checks import at_stops, in_pauses, pauses_hike, stops_hike
from test_cli import AT_STOP, stop_track

PO_DIR = os.path.join(ROOT, "po")
TEMPLATE = os.path.join(PO_DIR, "gpxfoto.pot")
POLISH = os.path.join(PO_DIR, "pl.po")
POLISH_PLURAL_FORMS = ("nplurals=3; plural=(n==1 ? 0 : n%10>=2 && n%10<=4 && "
                       "(n%100<10 || n%100>=20) ? 1 : 2);")
# The language of each file comes from its extension: Python or JavaScript
XGETTEXT = ["xgettext", "--files-from=po/POTFILES.in", "--from-code=UTF-8",
            "--keyword=N_", "--add-comments=Translators:", "--package-name=gpxfoto",
            "--package-version=0.1.0",
            "--msgid-bugs-address=https://github.com/tomaszbojanowski/gpxfoto/issues"]

TRACK_SINGULAR = "Track: {count} point, {start} – {end} (this computer’s time zone)"
TRACK_PLURAL = "Track: {count} points, {start} – {end} (this computer’s time zone)"
NO_POINTS_SINGULAR = "The GPX file contains no track points with timestamps."
NO_POINTS_PLURAL = "The GPX files contain no track points with timestamps."

BRACE_FIELD = re.compile(r"\{[^{}]*\}")
PERCENT_FIELD = re.compile(
    r"%(?:\([^)]*\))?[-#0 +]*(?:\*|\d+)?(?:\.(?:\*|\d+))?[diouxXeEfFgGcrsa%]")

needs_msgfmt = pytest.mark.skipif(shutil.which("msgfmt") is None,
                                  reason="msgfmt is not installed")
needs_xgettext = pytest.mark.skipif(shutil.which("xgettext") is None,
                                    reason="xgettext is not installed")


def read_lines(name):
    """Words of a po/ list file such as LINGUAS, without comments."""
    with open(os.path.join(PO_DIR, name), encoding="utf-8") as f:
        return [word for line in f for word in line.split("#", 1)[0].split()]


LANGUAGES = read_lines("LINGUAS")


def read_po(path):
    """Return (header, entries) of a PO or POT file.

    Each entry is a dict with "msgctxt" and "msgid_plural" (None when
    missing), "msgid", "msgstr" (a list with one string per form), "flags"
    and "comments" (the extracted "#." lines). The header entry also has
    "fields". Other comment lines, including obsolete #~ entries, are skipped.
    """
    entries, flags, comments, entry, field = [], set(), [], None, None
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line.startswith("#,"):
                flags.update(flag.strip() for flag in line[2:].split(","))
            elif line.startswith("#."):
                comments.append(line[2:].strip())
            elif not line or line.startswith("#"):
                continue
            elif line.startswith('"'):
                entry[field] += ast.literal_eval(line)
            else:
                field, text = line.split(" ", 1)
                if field == "msgctxt" or (field == "msgid" and (entry is None or "msgid" in entry)):
                    entry = {"flags": flags, "comments": comments}
                    flags, comments = set(), []
                    entries.append(entry)
                entry[field] = ast.literal_eval(text)
    result = []
    for entry in entries:
        forms = sorted((k for k in entry if k.startswith("msgstr")), key=lambda k: int(k[7:-1] or 0))
        result.append({"msgctxt": entry.get("msgctxt"), "msgid": entry["msgid"],
                       "msgid_plural": entry.get("msgid_plural"),
                       "msgstr": [entry[k] for k in forms], "flags": entry["flags"],
                       "comments": entry["comments"]})
    header = result.pop(0)
    assert header["msgid"] == "" and header["msgctxt"] is None
    header["fields"] = dict(line.split(": ", 1) for line in header["msgstr"][0].splitlines())
    return header, result


def message_key(entry):
    return entry["msgctxt"], entry["msgid"], entry["msgid_plural"]


def message_ids(path):
    return {message_key(entry) for entry in read_po(path)[1]}


def placeholders(text):
    """Brace fields and %-conversions; unnamed %-conversions keep their order."""
    percent = PERCENT_FIELD.findall(text)
    return (sorted(BRACE_FIELD.findall(text)),
            sorted(p for p in percent if p.startswith("%(")),
            [p for p in percent if not p.startswith("%(")])


def tool_environment():
    """Environment for the gettext tools, with their messages in English."""
    environment = {k: v for k, v in os.environ.items() if not k.startswith(("LC_", "LANG"))}
    environment["LC_ALL"] = "C"
    return environment


@pytest.fixture
def polish_mo(tmp_path):
    """Compile pl.po into tmp_path/pl/LC_MESSAGES/gpxfoto.mo; return tmp_path."""
    if shutil.which("msgfmt") is None:
        pytest.skip("msgfmt is not installed")
    directory = tmp_path / "pl" / "LC_MESSAGES"
    directory.mkdir(parents=True)
    subprocess.run(["msgfmt", "-o", str(directory / "gpxfoto.mo"), POLISH],
                   check=True, env=tool_environment())
    return tmp_path


@pytest.fixture
def polish_cli(polish_mo):
    """Run gpxfoto.cli.main() in-process in Polish, in Europe/Warsaw time.

    i18n.setup() changes the process-wide locale and gettext domain; both
    are restored afterwards, as are the environment and the time zone.
    """
    # Querying LC_ALL gives a composite name that also covers the categories
    # the locale module has no constants for (LC_PAPER, LC_NAME, ...).
    saved_locale = locale.setlocale(locale.LC_ALL)
    saved_domain = gettext.textdomain()
    saved_binding = gettext.bindtextdomain(i18n.DOMAIN)
    with pytest.MonkeyPatch.context() as patch:
        for name in list(os.environ):
            if name.startswith(("LC_", "LANG")):
                patch.delenv(name)
        patch.setenv("LANGUAGE", "pl")
        patch.setenv("LC_ALL", "C.UTF-8")
        patch.setenv("TZ", "Europe/Warsaw")
        patch.setenv("COLUMNS", "200")
        patch.delenv("FORCE_COLOR", raising=False)
        patch.setenv("PYTHON_COLORS", "0")
        patch.setenv("NO_COLOR", "1")
        patch.setattr(i18n, "LOCALE_DIR", str(polish_mo))
        time.tzset()

        def run(*args, **environment):
            """Run main() with args; None in environment removes a variable."""
            for name, value in environment.items():
                if value is None:
                    patch.delenv(name, raising=False)
                else:
                    patch.setenv(name, value)
            patch.setattr(sys, "argv", ["gpxfoto", *map(str, args)])
            cli.main()

        yield run
        gettext.bindtextdomain(i18n.DOMAIN, saved_binding)
        gettext.textdomain(saved_domain)
        locale.setlocale(locale.LC_ALL, saved_locale)
    time.tzset()


@pytest.fixture
def exiftool_present(monkeypatch):
    """Pass main()'s check for exiftool on paths that never run it."""
    which = shutil.which
    monkeypatch.setattr(shutil, "which", lambda name, *args, **kwargs: (
        "/usr/bin/exiftool" if name == "exiftool" else which(name, *args, **kwargs)))


# ---- Catalogs ---------------------------------------------------------------

def test_linguas_lists_exactly_the_po_files():
    assert "pl" in LANGUAGES
    po_files = sorted(name[:-3] for name in os.listdir(PO_DIR) if name.endswith(".po"))
    assert sorted(LANGUAGES) == po_files


def test_potfiles_lists_every_module_with_messages():
    marked = re.compile(r"\b(?:_|N_|ngettext)\(\s*[\"']")
    with_messages = set()
    for directory, subdirs, files in os.walk(os.path.join(ROOT, "gpxfoto")):
        subdirs[:] = [d for d in subdirs if d != "vendor"]
        for name in files:
            path = os.path.join(directory, name)
            if name.endswith((".py", ".js")):
                with open(path, encoding="utf-8") as f:
                    if marked.search(f.read()):
                        with_messages.add(os.path.relpath(path, ROOT).replace(os.sep, "/"))
    listed = read_lines("POTFILES.in")
    assert "gpxfoto/cli.py" in with_messages
    assert sorted(with_messages - set(listed)) == []
    assert [p for p in listed if not os.path.isfile(os.path.join(ROOT, p))] == []


@needs_xgettext
def test_template_is_up_to_date_with_the_sources(tmp_path):
    output = tmp_path / "gpxfoto.pot"
    subprocess.run([*XGETTEXT, f"--output={output}"], cwd=ROOT, env=tool_environment(),
                   check=True, capture_output=True)

    # Source references are left out: they only record line numbers
    def extracted(path):
        return {message_key(e): (e["flags"], e["comments"]) for e in read_po(path)[1]}

    assert extracted(output) == extracted(TEMPLATE)


def test_template_has_no_translations():
    header, entries = read_po(TEMPLATE)
    assert header["fields"]["Project-Id-Version"] == "gpxfoto 0.1.0"
    assert all(form == "" for entry in entries for form in entry["msgstr"])
    assert (None, TRACK_SINGULAR, TRACK_PLURAL) in message_ids(TEMPLATE)
    assert {"python-format", "python-brace-format"} <= set().union(*(e["flags"] for e in entries))
    assert any(c.startswith("Translators:") for e in entries for c in e["comments"])


def test_polish_header():
    fields = read_po(POLISH)[0]["fields"]
    assert fields["Language"] == "pl"
    assert fields["Content-Type"] == "text/plain; charset=UTF-8"
    assert fields["Plural-Forms"] == POLISH_PLURAL_FORMS


@pytest.mark.parametrize("language", LANGUAGES)
def test_catalog_has_the_messages_of_the_template(language):
    assert message_ids(os.path.join(PO_DIR, f"{language}.po")) == message_ids(TEMPLATE)


@pytest.mark.parametrize("language", LANGUAGES)
def test_every_message_is_translated(language):
    header, entries = read_po(os.path.join(PO_DIR, f"{language}.po"))
    plurals = int(re.search(r"nplurals=(\d+);", header["fields"]["Plural-Forms"]).group(1))
    # Babel refuses a catalog with a fuzzy header; Python's Tools/i18n/msgfmt.py drops it
    problems = [("fuzzy", "")] if "fuzzy" in header["flags"] else []
    for entry in entries:
        forms = plurals if entry["msgid_plural"] is not None else 1
        if len(entry["msgstr"]) != forms or "" in entry["msgstr"]:
            problems.append(("untranslated", entry["msgid"]))
        if "fuzzy" in entry["flags"]:
            problems.append(("fuzzy", entry["msgid"]))
    assert problems == []
    if language == "pl":
        assert plurals == 3


# Messages whose Polish translation is the English text itself
SAME_IN_POLISH = {"%(heading)s:", "argument %(argument_name)s: %(message)s", "{elevation} m",
                  "{seconds} s", "{minutes} min", "{hours} h {minutes} min", "{hours} h",
                  "{minutes} min {seconds} s", "{hours} h {minutes} min {seconds} s",
                  "UTC{offset}", "{metres} m", "{kilometres} km", "−1 h", "+1 h", "−1 s",
                  "+1 s"}


def test_polish_messages_are_not_copies_of_the_english_ones():
    copied = []
    for entry in read_po(POLISH)[1]:
        sources = [entry["msgid"]] + [entry["msgid_plural"]] * (len(entry["msgstr"]) - 1)
        copied += [source for source, translation in zip(sources, entry["msgstr"])
                   if translation == source and source not in SAME_IN_POLISH]
    assert copied == []


@pytest.mark.parametrize("language", LANGUAGES)
def test_no_obsolete_entries(language):
    with open(os.path.join(PO_DIR, f"{language}.po"), encoding="utf-8") as f:
        assert [line for line in f if line.startswith("#~")] == []


@pytest.mark.parametrize("language", LANGUAGES)
def test_format_flags_match_the_template(language):
    template = {message_key(e): e["flags"] for e in read_po(TEMPLATE)[1]}
    for entry in read_po(os.path.join(PO_DIR, f"{language}.po"))[1]:
        assert entry["flags"] == template[message_key(entry)], entry["msgid"]


@pytest.mark.parametrize("language", LANGUAGES)
def test_translations_keep_the_placeholders_and_line_breaks(language):
    entries = read_po(os.path.join(PO_DIR, f"{language}.po"))[1]
    assert any(BRACE_FIELD.search(e["msgid"]) for e in entries)
    assert any(PERCENT_FIELD.search(e["msgid"]) for e in entries)
    assert any("\n" in e["msgid"][:-1] for e in entries)
    mismatched = []
    for entry in entries:
        sources = [entry["msgid"]] + [entry["msgid_plural"]] * (len(entry["msgstr"]) - 1)
        # In a strftime format, % starts a date directive, which may change
        fields = 1 if "no-python-format" in entry["flags"] else 3
        for source, translation in zip(sources, entry["msgstr"]):
            if (placeholders(translation)[:fields] != placeholders(source)[:fields]
                    or translation.count("\n") != source.count("\n")):
                mismatched.append((source, translation))
    assert mismatched == []


@pytest.mark.parametrize("text, expected", [
    ("{a} and %(b)s, %s %d {a}", (["{a}", "{a}"], ["%(b)s"], ["%s", "%d"])),
    ("%(prog)s: error: %(message)s\n", ([], ["%(message)s", "%(prog)s"], [])),
    ("invalid %(type)s value: %(value)r", ([], ["%(type)s", "%(value)r"], [])),
    ("100%% sure, „{directory}”", (["{directory}"], [], ["%%"])),
    ("no fields", ([], [], [])),
])
def test_placeholder_helper(text, expected):
    assert placeholders(text) == expected


@needs_msgfmt
@pytest.mark.parametrize("language", LANGUAGES)
def test_msgfmt_check_accepts_the_catalog(tmp_path, language):
    process = subprocess.run(
        ["msgfmt", "--check", "--statistics", "-o", str(tmp_path / f"{language}.mo"),
         os.path.join(PO_DIR, f"{language}.po")],
        capture_output=True, text=True, env=tool_environment())
    assert process.returncode == 0, process.stderr
    count = len(read_po(TEMPLATE)[1])
    assert process.stderr.strip() == f"{count} translated messages."


# ---- Polish plural forms ----------------------------------------------------

@pytest.mark.parametrize("n, word", [
    (1, "punkt"), (2, "punkty"), (4, "punkty"), (5, "punktów"), (12, "punktów"),
    (22, "punkty"), (25, "punktów"), (101, "punktów"), (1803, "punkty"),
])
def test_polish_plural_forms(polish_mo, n, word):
    translation = gettext.translation(i18n.DOMAIN, str(polish_mo), languages=["pl"])
    track = translation.ngettext(TRACK_SINGULAR, TRACK_PLURAL, n)
    assert track.format(count=n, start="S", end="E") == \
        f"Trasa: {n} {word}, S – E (strefa czasowa komputera)"
    expected = ("Plik GPX nie zawiera punktów trasy ze znacznikami czasu." if n == 1
                else "Pliki GPX nie zawierają punktów trasy ze znacznikami czasu.")
    assert translation.ngettext(NO_POINTS_SINGULAR, NO_POINTS_PLURAL, n) == expected


# ---- Polish output of gpxfoto.cli.main() ------------------------------------

USAGE = ("użycie: gpxfoto [-h] -g TRASA [--write] [--offset SEKUNDY] [--timezone +GG:MM] "
         "[--clock-photo PLIK] [--clock-time CZAS] "
         "[--max-gap SEKUNDY] [--no-stops] [--overwrite] [--travel-direction] [--backup] [-r] "
         "[--ui] ZDJĘCIE [ZDJĘCIE ...]")
HELP = {
    "ZDJĘCIE": "pliki JPEG lub katalogi ze zdjęciami",
    "--help": "wyświetla ten komunikat pomocy i kończy działanie",
    "--gpx": "plik GPX z trasą lub katalog z plikami GPX; wtedy dla każdego zdjęcia wybierana "
             "jest trasa obejmująca czas jego wykonania (można podać wielokrotnie)",
    "--write": "zapisuje położenie w plikach (bez tej opcji wyświetlany jest tylko podgląd)",
    "--offset": "poprawka zegara aparatu w sekundach, dodawana do czasu wykonania zdjęcia",
    "--timezone": "strefa czasowa aparatu dla wszystkich zdjęć (domyślnie: odczytywana z danych "
                  "EXIF każdego zdjęcia)",
    "--clock-photo": "zdjęcie dokładnego zegara, na przykład zegarka zapisującego trasę, służące "
                     "do wyznaczenia poprawki zegara aparatu (razem z opcją --clock-time)",
    "--clock-time": "czas widoczny na zegarze na zdjęciu podanym w opcji --clock-photo, "
                    "w formacie 24-godzinnym, na przykład 14:03:27, 14:03:27+02:00 lub "
                    "2026-10-06T14:03:27+02:00",
    "--max-gap": "największy dopuszczalny odstęp czasu między zdjęciem a najbliższym punktem "
                 "trasy (domyślnie: 120 s)",
    "--no-stops": "nie wyszukuje postojów; każde zdjęcie otrzymuje położenie z trasy w chwili "
                  "wykonania",
    "--overwrite": "zmienia także zdjęcia, które mają już zapisane położenie",
    "--travel-direction": "dopisuje także kierunek ruchu z trasy (EXIF GPSTrack), a nie kierunek, "
                          "w którym skierowany był aparat; zdjęcia zrobione na postoju lub na "
                          "krętym odcinku go nie otrzymują",
    "--backup": "zachowuje kopie oryginalnych plików w podkatalogu „originals” obok każdego "
                "zdjęcia; istniejąca kopia nigdy nie jest zastępowana",
    "--recursive": "wyszukuje zdjęcia i trasy także w podkatalogach",
    "--ui": "otwiera zamiast tego interfejs w przeglądarce, w którym wybiera się zdjęcia i trasy; "
            "nie podaje się wtedy innych argumentów",
}


def split_usage(output):
    """Return the usage of argparse output as one line, and the lines after it.

    argparse wraps a long usage onto indented lines; where it wraps
    depends on the terminal width, so the lines are joined.
    """
    lines = output.splitlines()
    end = next((i for i, line in enumerate(lines) if i and not line.startswith(" ")), len(lines))
    return " ".join(line.strip() for line in lines[:end]), lines[end:]


def test_help_is_polish(polish_cli, capsys):
    with pytest.raises(SystemExit) as exit_info:
        polish_cli("--help")
    assert exit_info.value.code == 0
    output = capsys.readouterr().out
    usage, rest = split_usage(output)
    assert usage == USAGE
    assert rest[:2] == ["", "Dopisuje do zdjęć położenie na podstawie tras GPX, "
                            "nie zmieniając danych obrazu."]
    assert "argumenty pozycyjne:" in rest
    assert "opcje:" in rest
    # "-g GPX, --gpx GPX" before Python 3.13, "-g, --gpx GPX" since
    help_texts = {}
    for line in rest:
        if line.startswith("  "):
            invocation, text = re.split(r"\s{2,}", line.strip(), maxsplit=1)
            option = re.search(r"--[\w-]+", invocation)
            help_texts[option.group() if option else invocation] = text
    assert help_texts == HELP
    english = [e["msgid"] for e in read_po(POLISH)[1]
               if e["msgstr"][0] != e["msgid"] and placeholders(e["msgid"]) == ([], [], [])]
    # As whole words: "Track" is a message of its own, but also part of GPSTrack
    assert [message for message in english
            if re.search(r"(?<!\w)" + re.escape(message) + r"(?!\w)", output)] == []


@pytest.mark.parametrize("args, message", [
    (["a.jpg"], "wymagane są następujące argumenty: -g/--gpx"),
    (["-g", "t.gpx", "a.jpg", "--bogus"], "nierozpoznane argumenty: --bogus"),
    (["-g", "t.gpx", "a.jpg", "--offset", "abc"],
     "argument --offset: nieprawidłowa liczba sekund: abc"),
    (["a.jpg", "-g"], "argument -g/--gpx: oczekiwano jednego argumentu"),
    (["-g", "t.gpx", "a.jpg", "--write=yes"],
     "argument --write: zignorowano jawnie podany argument 'yes'"),
])
def test_argparse_errors_are_polish(polish_cli, capsys, args, message):
    with pytest.raises(SystemExit) as exit_info:
        polish_cli(*args)
    assert exit_info.value.code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert split_usage(captured.err) == (USAGE, [f"gpxfoto: błąd: {message}"])
    assert captured.err.endswith("\n")


def test_missing_exiftool_message_is_polish(polish_cli, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name, *args, **kwargs: None)
    with pytest.raises(SystemExit) as exit_info:
        polish_cli("-g", "t.gpx", "a.jpg")
    assert exit_info.value.code == (
        "Program exiftool nie jest zainstalowany; gpxfoto potrzebuje go do odczytywania "
        "i zapisywania metadanych zdjęć.\n"
        "W systemie Fedora można go zainstalować poleceniem: "
        "sudo dnf install perl-Image-ExifTool\n"
        "W systemie macOS można go zainstalować poleceniem: brew install exiftool")


def test_invalid_timezone_message_is_polish(polish_cli, exiftool_present):
    with pytest.raises(SystemExit) as exit_info:
        polish_cli("-g", "t.gpx", "a.jpg", "--timezone", "2h")
    assert exit_info.value.code == ("Strefę czasową należy podać w postaci +GG:MM, na przykład "
                                    "+02:00 lub -05:00.")


@pytest.mark.parametrize("files, message", [
    (1, "Plik GPX nie zawiera punktów trasy ze znacznikami czasu."),
    (2, "Pliki GPX nie zawierają punktów trasy ze znacznikami czasu."),
])
def test_empty_track_message_is_polish(polish_cli, exiftool_present, tmp_path, files, message):
    args = []
    for i in range(files):
        args += ["-g", write_gpx(tmp_path / f"{i}.gpx", [(None, 50.0, 20.0, None)])]
    with pytest.raises(SystemExit) as exit_info:
        polish_cli(*args, "a.jpg")
    assert exit_info.value.code == message


@pytest.mark.parametrize("count, word", [(1, "punkt"), (4, "punkty"), (12, "punktów"),
                                         (22, "punkty")])
def test_track_summary_uses_polish_plurals(polish_cli, exiftool_present, tmp_path, capsys,
                                           count, word):
    gpx = write_gpx(tmp_path / "track.gpx",
                    [(f"2024-05-01T10:00:{i:02d}Z", 50.0, 20.0, None) for i in range(count)])
    (tmp_path / "photos").mkdir()
    with pytest.raises(SystemExit) as exit_info:
        polish_cli("-g", gpx, tmp_path / "photos")
    assert exit_info.value.code == "Nie znaleziono zdjęć JPEG."
    assert capsys.readouterr().out == (f"Trasa track.gpx: {count} {word}, 1.05.2024 12:00:00 – "
                                       f"1.05.2024 12:00:{count - 1:02d} (strefa czasowa komputera)\n")


# The system's own formats differ: 05/01/24 in C, 01.05.2024 in glibc's
# pl_PL and 2024.05.01 in the pl_PL of macOS
@pytest.mark.parametrize("regional", ["C.UTF-8", "pl_PL.UTF-8"])
def test_polish_dates_do_not_depend_on_the_system(polish_cli, exiftool_present, tmp_path,
                                                  capsys, regional):
    try:
        locale.setlocale(locale.LC_TIME, regional)
    except locale.Error:
        pytest.skip(f"the {regional} locale is not installed")
    gpx = write_gpx(tmp_path / "track.gpx", [("2026-10-06T07:28:09Z", 50.0, 20.0, None),
                                             ("2026-10-16T13:02:09Z", 50.0, 20.0, None)])
    (tmp_path / "photos").mkdir()
    with pytest.raises(SystemExit):
        polish_cli("-g", gpx, tmp_path / "photos", LC_ALL=regional)
    assert capsys.readouterr().out == ("Trasa track.gpx: 2 punkty, 6.10.2026 09:28:09 – "
                                       "16.10.2026 15:02:09 "
                                       "(strefa czasowa komputera)\n")


def test_missing_photo_message_is_polish(polish_cli, exiftool_present, tmp_path):
    gpx = write_gpx(tmp_path / "track.gpx", [("2024-05-01T10:00:00Z", 50.0, 20.0, None)])
    missing = tmp_path / "missing.jpg"
    with pytest.raises(SystemExit) as exit_info:
        polish_cli("-g", gpx, missing)
    assert exit_info.value.code == f"Nie ma takiego pliku ani katalogu: {missing}"


def test_unreadable_gpx_message_is_polish(polish_cli, exiftool_present, tmp_path):
    gpx = tmp_path / "track.gpx"
    gpx.write_text("<gpx>", encoding="utf-8")
    with pytest.raises(SystemExit) as exit_info:
        polish_cli("-g", gpx, tmp_path / "a.jpg")
    assert exit_info.value.code == (f"Nie można odczytać pliku GPX {gpx}: "
                                    "no element found: line 1, column 5")


@needs_exiftool
def test_skipped_track_file_message_is_polish(polish_cli, tmp_path, capsys):
    tracks = tmp_path / "tracks"
    tracks.mkdir()
    write_gpx(tracks / "day1.gpx", TRACK)
    (tracks / "broken.gpx").write_text("<!DOCTYPE gpx>\n<gpx><trk>")
    photo(tmp_path / "photos", "a.jpg", "-DateTimeOriginal=2024:05:01 12:00:50",
          "-OffsetTimeOriginal=+02:00")
    with pytest.raises(SystemExit) as exit_info:
        polish_cli("-g", tracks, tmp_path / "photos")
    assert exit_info.value.code == 1
    captured = capsys.readouterr()
    error, skipped = captured.err.splitlines()
    assert error.startswith(f"Nie można odczytać pliku GPX {tracks / 'broken.gpx'}: ")
    assert skipped == "  Ten plik zostaje pominięty; używane są pozostałe trasy."
    assert "Dopasowano: 1, pominięto: 0" in captured.out.splitlines()


def test_exiftool_without_output_message_is_polish(polish_cli, exiftool_present, tmp_path,
                                                    monkeypatch):
    gpx = write_gpx(tmp_path / "track.gpx", [("2024-05-01T10:00:00Z", 50.0, 20.0, None)])
    (tmp_path / "a.jpg").write_bytes(make_jpeg())
    monkeypatch.setattr("gpxfoto.engine.photos.subprocess", types.SimpleNamespace(
        run=lambda command, **kwargs: subprocess.CompletedProcess(command, 1, "", "Error: x\n")))
    with pytest.raises(SystemExit) as exit_info:
        polish_cli("-g", gpx, tmp_path / "a.jpg")
    assert exit_info.value.code == "Program exiftool nie działa:\nError: x\n"


# Europe/Warsaw is UTC+2 on this date; the C locale writes dates as MM/DD/YY.
TRACK = [
    ("2024-05-01T10:00:00Z", 50.0, 20.0, 200.0),
    ("2024-05-01T10:01:40Z", 50.001, 20.002, 210.0),
    ("2024-05-01T11:00:00Z", 50.1, 20.1, 300.0),
]
TRACK_LINE = ("Trasa track.gpx: 3 punkty, 1.05.2024 12:00:00 – 1.05.2024 13:00:00 "
              "(strefa czasowa komputera)")


def photo(directory, name, *tags):
    path = directory / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(make_jpeg())
    if tags:
        set_tags(path, *tags)
    return path


@needs_exiftool
def test_preview_is_polish(polish_cli, tmp_path, capsys):
    gpx = write_gpx(tmp_path / "track.gpx", TRACK)
    photos = tmp_path / "photos"
    photo(photos, "a.jpg", "-DateTimeOriginal=2024:05:01 12:00:50", "-OffsetTimeOriginal=+02:00")
    photo(photos, "b.jpg", "-DateTimeOriginal=2024:05:01 12:01:00")
    photo(photos, "c.jpg", "-DateTimeOriginal=2024:05:01 12:30:00", "-OffsetTimeOriginal=+02:00")
    photo(photos, "d.jpg")
    photo(photos, "e.jpg", "-DateTimeOriginal=2024:05:01 12:00:50", "-GPSLatitude=1",
          "-GPSLatitudeRef=N", "-GPSLongitude=2", "-GPSLongitudeRef=E")
    photo(photos, "f.jpg", "-DateTimeOriginal=2024:05:01 11:58:30", "-OffsetTimeOriginal=+02:00")
    photo(photos, "g.jpg", "-DateTimeOriginal=2024:05:01 15:30:00", "-OffsetTimeOriginal=+02:00")
    photo(photos, "h.jpg", "-DateTimeOriginal#=0000:00:00 00:00:00")
    # Below 120 s, so that a skipped photo can be less than two minutes away
    polish_cli("-g", gpx, photos, "--max-gap", "60")
    assert capsys.readouterr().out.splitlines() == [
        TRACK_LINE,
        "  a.jpg            12:00:50  50.000500, 20.001000    205 m",
        "  b.jpg            12:01:00  50.000600, 20.001200    206 m"
        "  [strefa czasowa komputera (brak w EXIF)]",
        "  c.jpg            12:30:00  pominięte: przerwa w zapisie trasy, najbliższy punkt "
        "oddalony o 28 min",
        "  d.jpg            pominięte: brak daty wykonania w EXIF",
        "  e.jpg            pominięte: ma już zapisane położenie",
        "  f.jpg            11:58:30  pominięte: 90 s przed początkiem trasy",
        "  g.jpg            15:30:00  pominięte: 2 h 30 min po zakończeniu trasy",
        "  h.jpg            pominięte: nieprawidłowa data wykonania w EXIF: 0000:00:00 00:00:00",
        "Dopasowano: 2, pominięto: 6",
        "To był podgląd, nie zmieniono żadnych plików. Aby zapisać położenie, należy użyć "
        "opcji --write.",
    ]


@needs_exiftool
def test_clock_correction_is_polish(polish_cli, tmp_path, capsys):
    gpx = write_gpx(tmp_path / "track.gpx", TRACK)
    watch = photo(tmp_path, "zegar.jpg", "-DateTimeOriginal=2024:05:01 11:58:38")
    photo(tmp_path / "photos", "a.jpg", "-DateTimeOriginal=2024:05:01 11:58:38",
          "-OffsetTimeOriginal=+02:00")
    polish_cli("-g", gpx, tmp_path / "photos", "--clock-photo", watch, "--clock-time", "12:00:50")
    assert capsys.readouterr().out.splitlines()[:4] == [
        TRACK_LINE,
        "Poprawka zegara: +2 min 12 s (odpowiada opcji --offset=132)",
        "Zdjęcie zegara zegar.jpg: aparat 1.05.2024 11:58:38 UTC+02:00, zegar 1.05.2024 12:00:50 "
        "UTC+02:00  [strefa czasowa komputera (brak w EXIF)]",
        "  a.jpg            12:00:50  50.000500, 20.001000    205 m",
    ]


@needs_exiftool
@pytest.mark.parametrize("reading, message", [
    ("13:00:50",
     "Czas na zegarze różni się od czasu wykonania zdjęcia zegara o 1 h 2 min 12 s, więc zegar "
     "mógł pokazywać czas innej strefy czasowej niż aparat. Należy dopisać przesunięcie względem "
     "UTC czasu na zegarze, na przykład „13:00:50+03:00” lub „13:00:50+02:00”."),
    ("14:00:50+09:00",
     "Czas na zegarze różni się od czasu wykonania zdjęcia zegara o 4 h 57 min 48 s. Jeśli zegar "
     "aparatu rzeczywiście tak bardzo się myli, należy podać także datę widoczną na zegarze "
     "w postaci „RRRR-MM-DDT14:00:50+09:00”."),
])
def test_clock_errors_are_polish(polish_cli, tmp_path, capsys, reading, message):
    gpx = write_gpx(tmp_path / "track.gpx", TRACK)
    watch = photo(tmp_path, "zegar.jpg", "-DateTimeOriginal=2024:05:01 11:58:38",
                  "-OffsetTimeOriginal=+02:00")
    with pytest.raises(SystemExit) as exit_info:
        polish_cli("-g", gpx, tmp_path, "--clock-photo", watch, "--clock-time", reading)
    assert exit_info.value.code == message
    assert capsys.readouterr().out == ""


@needs_exiftool
@pytest.mark.parametrize("count, have", [(1, "zdjęcie ma"), (2, "zdjęcia mają"),
                                         (5, "zdjęć ma")])
def test_time_check_summary_is_polish(polish_cli, tmp_path, capsys, count, have):
    gpx = write_gpx(tmp_path / "track.gpx", TRACK)
    for i in range(count):
        path = photo(tmp_path / "photos", f"p{i}.jpg", "-DateTimeOriginal=2024:05:01 12:00:50")
        set_panasonic_time_stamp(path, "2024:05:01 10:00:50")
    polish_cli("-g", gpx, tmp_path / "photos", "--timezone", "+01:00")
    lines = capsys.readouterr().out.splitlines()
    assert lines[1] == ("  p0.jpg           12:00:50  50.100000, 20.100000    300 m  [strefa "
                        "czasowa z opcji --timezone; czas UTC z aparatu wskazuje strefę +02:00]")
    assert lines[count + 1:count + 5] == [
        f"Dopasowano: {count}, pominięto: 0",
        f"Ostrzeżenie: {count} {have} czas wykonania niezgodny z czasem UTC zapisanym przez "
        "aparat.",
        "  Błędna jest strefa czasowa podana w opcji --timezone albo ustawienie strefy czasowej "
        "w aparacie.",
        "  Oba czasy byłyby zgodne przy opcji --timezone=+02:00.",
    ]


@needs_exiftool
def test_stop_note_is_polish(polish_cli, tmp_path, capsys):
    gpx = write_gpx(tmp_path / "stop.gpx", stop_track())
    path = photo(tmp_path / "photos", "b.jpg", "-DateTimeOriginal=2024:05:01 12:03:30",
                 "-OffsetTimeOriginal=+02:00")
    polish_cli("-g", gpx, path)
    assert capsys.readouterr().out.splitlines()[1:4] == [
        AT_STOP.replace("[stop", "[postój"), "Dopasowano: 1, pominięto: 0",
        "Na postojach: 1 z 1 dopasowanego zdjęcia"]


@pytest.mark.parametrize("count, text", [
    (1, "1 z 1 dopasowanego zdjęcia"), (2, "2 z 2 dopasowanych zdjęć"),
    (5, "5 z 5 dopasowanych zdjęć"), (22, "22 z 22 dopasowanych zdjęć"),
])
def test_count_during_stops_is_polish(polish_cli, exiftool_present, tmp_path, capsys,
                                      monkeypatch, count, text):
    gpx = write_gpx(tmp_path / "stop.gpx", stop_track())
    metadata = [{"SourceFile": f"{i}.jpg", "DateTimeOriginal": "2024:05:01 12:03:30",
                 "OffsetTimeOriginal": "+02:00"} for i in range(count)]
    monkeypatch.setattr(cli, "check_exiftool", lambda: None)
    monkeypatch.setattr(cli, "find_photos", lambda paths, recursive: [
        m["SourceFile"] for m in metadata])
    monkeypatch.setattr(cli, "read_metadata", lambda files: metadata)
    polish_cli("-g", gpx, "photos")
    assert capsys.readouterr().out.splitlines()[count + 2] == "Na postojach: " + text


@needs_exiftool
def test_directory_of_tracks_is_polish(polish_cli, tmp_path, capsys):
    tracks = tmp_path / "trasy"
    tracks.mkdir()
    write_gpx(tracks / "dzien1.gpx", TRACK)
    write_gpx(tracks / "dzien2.gpx", [("2024-05-02T10:00:00Z", 51.0, 21.0, None)])
    path = photo(tmp_path / "photos", "a.jpg", "-DateTimeOriginal=2024:05:01 12:00:50",
                 "-OffsetTimeOriginal=+02:00")
    polish_cli("-g", tracks, path)
    assert capsys.readouterr().out.splitlines()[:3] == [
        "Trasy obejmujące czas zdjęć: 1 z 2 plików GPX",
        TRACK_LINE.replace("track.gpx", "dzien1.gpx"),
        "  a.jpg            12:00:50  50.000500, 20.001000    205 m  dzien1.gpx",
    ]


def test_no_gpx_files_message_is_polish(polish_cli, exiftool_present, tmp_path):
    with pytest.raises(SystemExit) as exit_info:
        polish_cli("-g", tmp_path, tmp_path / "a.jpg")
    assert exit_info.value.code == "Nie znaleziono plików GPX."


@needs_exiftool
def test_write_summary_and_errors_are_polish(polish_cli, tmp_path, capsys):
    gpx = write_gpx(tmp_path / "track.gpx", [(t, lat, lon, None) for t, lat, lon, _ in TRACK])
    for name in ("a.jpg", "b.jpg", "c.jpg"):
        photo(tmp_path / "photos", name, "-DateTimeOriginal=2024:05:01 12:00:50")
    # exiftool still reads the metadata, but the image data is cut off
    damaged = tmp_path / "photos" / "b.jpg"
    damaged.write_bytes(damaged.read_bytes().split(b"\xff\xda")[0])
    original = damaged.read_bytes()
    with pytest.raises(SystemExit) as exit_info:
        polish_cli("-g", gpx, tmp_path / "photos", "--timezone", "+02:00", "--write")
    assert exit_info.value.code == 1
    matched = "12:00:50  50.000500, 20.001000        —  [strefa czasowa z opcji --timezone]"
    assert capsys.readouterr().out.splitlines() == [
        TRACK_LINE,
        f"  a.jpg            {matched}",
        f"  b.jpg            {matched}",
        f"  c.jpg            {matched}",
        "Dopasowano: 3, pominięto: 0",
        "  Nie można zapisać pliku b.jpg: uszkodzona struktura JPEG (plik bez zmian)",
        "Zapisano: 2, błędy: 1",
        "Sprawdzono, że dane obrazu w każdym zapisanym pliku pozostały bez zmian.",
    ]
    assert damaged.read_bytes() == original


def locale_exists(name):
    saved = locale.setlocale(locale.LC_ALL)
    try:
        locale.setlocale(locale.LC_ALL, name)
        return True
    except locale.Error:
        return False
    finally:
        locale.setlocale(locale.LC_ALL, saved)


@needs_exiftool
@pytest.mark.skipif(not locale_exists("pl_PL.UTF-8"), reason="the pl_PL.UTF-8 locale is missing")
def test_polish_locale_without_language_variable(polish_cli, tmp_path, capsys):
    """The language comes from LC_ALL; numbers have a decimal comma, hence "; "."""
    gpx = write_gpx(tmp_path / "track.gpx", [
        (f"2024-05-01T10:{i // 60:02d}:{i % 60:02d}Z", 50 + i / 100000, 20 + i / 50000,
         200 + i / 10) for i in range(1803)])
    photo(tmp_path / "photos", "a.jpg", "-DateTimeOriginal=2024:05:01 12:00:50",
          "-OffsetTimeOriginal=+02:00")
    polish_cli("-g", gpx, tmp_path / "photos", LC_ALL="pl_PL.UTF-8", LANGUAGE=None)
    thousands = locale.localeconv()["thousands_sep"]
    assert capsys.readouterr().out.splitlines() == [
        f"Trasa track.gpx: 1{thousands}803 punkty, 1.05.2024 12:00:00 – 1.05.2024 12:30:02 "
        "(strefa czasowa komputera)",
        "  a.jpg            12:00:50  50,000500; 20,001000    205 m",
        "Dopasowano: 1, pominięto: 0",
        "To był podgląd, nie zmieniono żadnych plików. Aby zapisać położenie, należy użyć "
        "opcji --write.",
    ]
    assert thousands.isspace()


@needs_exiftool
def test_shift_warning_is_polish(polish_cli, tmp_path, capsys, photo_series):
    hike = hike_gpx(tmp_path / "hike.gpx", stops_hike())
    photo_series(at_stops(-3600))
    polish_cli("photos", "-g", hike)
    assert capsys.readouterr().out.splitlines()[-4:-1] == [
        "Ostrzeżenie: po przesunięciu czasu zdjęć o +1 h wyraźnie więcej zdjęć wypada na "
        "postojach: 12 z 12 zamiast 0.",
        "  Różnica dokładnie jednej godziny zwykle oznacza, że w aparacie nie przestawiono czasu "
        "na letni lub zimowy albo że ustawiono w nim złą strefę czasową.",
        "  Aby zastosować tę poprawkę, należy uruchomić program ponownie z opcją --offset=3600 "
        "albo --timezone=+01:00.",
    ]


@needs_exiftool
def test_motion_and_jump_warnings_are_polish(polish_cli, tmp_path, capsys, photo_series):
    # 20 photos while walking, by a clock 90 s behind, and one more taken
    # 4 s after the first, with a time zone an hour behind
    walk = hike_gpx(tmp_path / "walk.gpx", pauses_hike())
    times = in_pauses(20, -90)
    photo_series(times + [times[0] + 4 + 3600], zone=["+02:00"] * 20 + ["+01:00"])
    polish_cli("photos", "-g", walk)
    # The decimal point follows the locale, here C.UTF-8
    assert capsys.readouterr().out.splitlines()[-6:-1] == [
        "Ostrzeżenie: zegar aparatu może być przesunięty. 21 z 21 dopasowanych zdjęć zrobiono, "
        "gdy według trasy poruszano się pełnym tempem.",
        "  Zdjęcia robi się zwykle na postojach albo przy zwalnianiu. Należy sprawdzić zegar "
        "aparatu, na przykład za pomocą zdjęcia zegarka, który zapisuje trasę, i opcji "
        "--clock-photo oraz --clock-time.",
        "Ostrzeżenie: zdjęcia zrobione w odstępie krótszym niż minuta są umieszczone "
        "nieprawdopodobnie daleko od siebie:",
        "  p01.jpg i p21.jpg: zrobione w odstępie 4 s, umieszczone 4.1 km od siebie, strefy "
        "czasowe UTC+02:00 i UTC+01:00",
        "  Należy sprawdzić strefy czasowe tych zdjęć oraz to, czy pliki GPX nie zapisują różnych "
        "wycieczek w tym samym czasie.",
    ]


@needs_exiftool
def test_direction_of_travel_is_polish(polish_cli, tmp_path, capsys):
    gpx = write_gpx(tmp_path / "track.gpx", stop_track())
    photos = tmp_path / "photos"
    photo(photos, "a.jpg", "-DateTimeOriginal=2024:05:01 12:00:50", "-OffsetTimeOriginal=+02:00")
    photo(photos, "b.jpg", "-DateTimeOriginal=2024:05:01 12:03:30", "-OffsetTimeOriginal=+02:00")
    photo(photos, "c.jpg", "-DateTimeOriginal=2024:05:01 12:06:55", "-OffsetTimeOriginal=+02:00")
    polish_cli("-g", gpx, photos, "--travel-direction")
    lines = capsys.readouterr().out.splitlines()
    assert lines[1].endswith("    200 m  kierunek ruchu  90°")
    assert lines[2].endswith("  [postój 12:01:52 – 12:05:08; brak kierunku ruchu: zrobione "
                             "podczas postoju]")
    assert lines[3].endswith("    200 m  [brak kierunku ruchu: zbyt blisko początku lub końca "
                             "trasy]")
    assert lines[5] == "Z kierunkiem ruchu: 1, bez kierunku: 2"


def test_catalog_for_the_page_is_polish(polish_mo, monkeypatch):
    monkeypatch.setenv("LANGUAGE", "pl")
    monkeypatch.setattr(i18n, "LOCALE_DIR", str(polish_mo))
    catalog = i18n.catalog()
    assert catalog["language"] == "pl"
    assert catalog["messages"]["Matched: {matched}, skipped: {skipped}"] == (
        "Dopasowano: {matched}, pominięto: {skipped}")
    assert catalog["messages"]["During stops: {count} of {matched} matched photo"] == [
        "Na postojach: {count} z {matched} dopasowanego zdjęcia",
        "Na postojach: {count} z {matched} dopasowanych zdjęć",
        "Na postojach: {count} z {matched} dopasowanych zdjęć"]
    assert "" not in catalog["messages"]
    assert [catalog["plural"][n] for n in (0, 1, 2, 5, 12, 22, 101, 112, 122)] == [
        2, 0, 1, 2, 2, 1, 2, 2, 1]
    assert len(catalog["plural"]) == 200


def test_catalog_for_the_page_without_a_translation(monkeypatch, tmp_path):
    monkeypatch.setenv("LANGUAGE", "pl")
    monkeypatch.setattr(i18n, "LOCALE_DIR", str(tmp_path))
    catalog = i18n.catalog()
    assert (catalog["language"], catalog["messages"]) == ("en", {})
    assert catalog["plural"][:3] == [1, 0, 1]


@pytest.mark.parametrize("hint, total, outside, expected", [
    (ShiftHint(3600, 12, 12, 6, 0), 12, 6,
     "Teraz żadne zdjęcie nie wypada na postoju, a 6 z 12 leży poza czasem trasy. "
     "Po przesunięciu o +1 h wszystkie 12 trafia na postoje."),
    (ShiftHint(3600, 22, 22, 6, 2), 22, 3,
     "Teraz 2 zdjęcia wypadają na postojach, a 3 z 22 leżą poza czasem trasy. "
     "Po przesunięciu o +1 h wszystkie 22 trafiają na postoje."),
    (ShiftHint(3600, 11, 14, 6, 1), 14, 1,
     "Teraz 1 zdjęcie wypada na postoju, a 1 z 14 leży poza czasem trasy. "
     "Po przesunięciu o +1 h 11 z 14 trafia na postoje."),
    (ShiftHint(-3600, 13, 15, 6, 5), 15, 0,
     "Teraz 5 zdjęć wypada na postojach. Po przesunięciu o −1 h 13 z 15 trafia na postoje."),
    (ShiftHint(-3600, 12, 15, 6, 0), 15, 0,
     "Teraz żadne zdjęcie nie wypada na postoju. "
     "Po przesunięciu o −1 h 12 z 15 trafia na postoje."),
    (ShiftHint(-3600, 23, 25, 6, 0), 25, 0,
     "Teraz żadne zdjęcie nie wypada na postoju. "
     "Po przesunięciu o −1 h 23 z 25 trafiają na postoje."),
])
def test_the_card_of_a_shift_uses_polish_plurals(polish_mo, monkeypatch, hint, total, outside,
                                                 expected):
    polish = gettext.translation(i18n.DOMAIN, str(polish_mo), languages=["pl"])
    monkeypatch.setattr(warnings, "_", polish.gettext)
    monkeypatch.setattr(warnings, "ngettext", polish.ngettext)
    assert warnings._shift_text(hint, total, outside) == expected
