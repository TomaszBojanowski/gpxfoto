"""Wyszukiwanie zdjęć, odczyt metadanych i czasu wykonania."""
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone

ROZSZERZENIA = {".jpg", ".jpeg"}


def znajdz_zdjecia(sciezki, rekurencyjnie):
    wynik = []
    for s in sciezki:
        if os.path.isdir(s):
            for katalog, podkatalogi, pliki in os.walk(s):
                podkatalogi.sort()
                for p in sorted(pliki):
                    if os.path.splitext(p)[1].lower() in ROZSZERZENIA:
                        wynik.append(os.path.join(katalog, p))
                if not rekurencyjnie:
                    break
        elif os.path.isfile(s):
            wynik.append(s)
        else:
            sys.exit(f"Nie ma takiego pliku ani katalogu: {s}")
    return wynik


def odczytaj_metadane(pliki):
    wynik = []
    for start in range(0, len(pliki), 500):
        polecenie = ["exiftool", "-json", "-n", "-DateTimeOriginal", "-CreateDate",
                     "-OffsetTimeOriginal", "-OffsetTime", "-SubSecTimeOriginal",
                     "-GPSLatitude", "-GPSLongitude", "--"] + pliki[start:start + 500]
        proces = subprocess.run(polecenie, capture_output=True, text=True)
        if not proces.stdout.strip():
            sys.exit("exiftool nie zwrócił danych:\n" + proces.stderr)
        wynik.extend(json.loads(proces.stdout))
    return wynik


def strefa_z_tekstu(tekst):
    znak = -1 if tekst[0] == "-" else 1
    g, m = tekst.lstrip("+-").split(":")
    return timezone(znak * timedelta(hours=int(g), minutes=int(m)))


def czas_zdjecia(meta, strefa_reczna):
    """Zwraca (datetime ze strefą, opis źródła strefy) albo (None, powód)."""
    surowy = meta.get("DateTimeOriginal") or meta.get("CreateDate")
    if not surowy:
        return None, "brak daty wykonania w EXIF"
    try:
        czas = datetime.strptime(str(surowy)[:19], "%Y:%m:%d %H:%M:%S")
    except ValueError:
        return None, f"nieczytelna data: {surowy}"
    ulamek = str(meta.get("SubSecTimeOriginal", "")).strip()
    if ulamek.isdigit():
        czas += timedelta(seconds=float("0." + ulamek))
    przesuniecie = meta.get("OffsetTimeOriginal") or meta.get("OffsetTime")
    if strefa_reczna is not None:
        return czas.replace(tzinfo=strefa_reczna), "ręczna"
    if przesuniecie:
        try:
            return czas.replace(tzinfo=strefa_z_tekstu(str(przesuniecie))), "z aparatu"
        except (ValueError, IndexError):
            pass
    return czas.astimezone(), "systemowa (brak w EXIF)"
