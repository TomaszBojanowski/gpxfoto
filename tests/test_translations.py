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

from conftest import ROOT, make_jpeg, needs_exiftool, set_tags, write_gpx
from gpxfoto import cli, i18n

PO_DIR = os.path.join(ROOT, "po")
TEMPLATE = os.path.join(PO_DIR, "gpxfoto.pot")
POLISH = os.path.join(PO_DIR, "pl.po")
POLISH_PLURAL_FORMS = ("nplurals=3; plural=(n==1 ? 0 : n%10>=2 && n%10<=4 && "
                       "(n%100<10 || n%100>=20) ? 1 : 2);")
XGETTEXT = ["xgettext", "--files-from=po/POTFILES.in", "--from-code=UTF-8", "--language=Python",
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
    for directory, _subdirs, files in os.walk(os.path.join(ROOT, "gpxfoto")):
        for name in files:
            path = os.path.join(directory, name)
            if name.endswith(".py"):
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
                  "{minutes} min {seconds} s", "{hours} h {minutes} min {seconds} s"}


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

USAGE = ("użycie: gpxfoto [-h] -g PLIK [--write] [--offset SEKUNDY] [--timezone +GG:MM] "
         "[--max-gap SEKUNDY] [--overwrite] [--backup] [-r] ZDJĘCIE [ZDJĘCIE ...]")
HELP = {
    "ZDJĘCIE": "pliki JPEG lub katalogi ze zdjęciami",
    "--help": "wyświetla ten komunikat pomocy i kończy działanie",
    "--gpx": "plik GPX z trasą (można podać wielokrotnie)",
    "--write": "zapisuje położenie w plikach (bez tej opcji wyświetlany jest tylko podgląd)",
    "--offset": "poprawka zegara aparatu w sekundach, dodawana do czasu wykonania zdjęcia",
    "--timezone": "strefa czasowa aparatu dla wszystkich zdjęć (domyślnie: odczytywana z danych "
                  "EXIF każdego zdjęcia)",
    "--max-gap": "największy dopuszczalny odstęp czasu między zdjęciem a najbliższym punktem "
                 "trasy (domyślnie: 120 s)",
    "--overwrite": "zmienia także zdjęcia, które mają już zapisane położenie",
    "--backup": "zachowuje kopie oryginalnych plików w podkatalogu „originals” obok każdego "
                "zdjęcia; istniejąca kopia nigdy nie jest zastępowana",
    "--recursive": "wyszukuje zdjęcia także w podkatalogach",
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
    lines = output.splitlines()
    assert "argumenty pozycyjne:" in lines
    assert "opcje:" in lines
    # "-g GPX, --gpx GPX" before Python 3.13, "-g, --gpx GPX" since
    help_texts = {}
    for line in lines:
        if line.startswith("  "):
            invocation, text = re.split(r"\s{2,}", line.strip(), maxsplit=1)
            option = re.search(r"--[\w-]+", invocation)
            help_texts[option.group() if option else invocation] = text
    assert help_texts == HELP
    english = [e["msgid"] for e in read_po(POLISH)[1]
               if e["msgstr"][0] != e["msgid"] and placeholders(e["msgid"]) == ([], [], [])]
    assert [message for message in english if message in output] == []


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
    assert exit_info.value.code == ("Program exiftool nie jest zainstalowany. W systemie Fedora "
                                    "można go zainstalować poleceniem: "
                                    "sudo dnf install perl-Image-ExifTool")


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
    assert capsys.readouterr().out == (f"Trasa: {count} {word}, 1.05.2024 12:00:00 – "
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
    assert capsys.readouterr().out == ("Trasa: 2 punkty, 6.10.2026 09:28:09 – 16.10.2026 15:02:09 "
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
TRACK_LINE = ("Trasa: 3 punkty, 1.05.2024 12:00:00 – 1.05.2024 13:00:00 "
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
        f"Trasa: 1{thousands}803 punkty, 1.05.2024 12:00:00 – 1.05.2024 12:30:02 "
        "(strefa czasowa komputera)",
        "  a.jpg            12:00:50  50,000500; 20,001000    205 m",
        "Dopasowano: 1, pominięto: 0",
        "To był podgląd, nie zmieniono żadnych plików. Aby zapisać położenie, należy użyć "
        "opcji --write.",
    ]
    assert thousands.isspace()
