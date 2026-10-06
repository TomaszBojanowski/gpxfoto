#!/usr/bin/env python3
"""Geotagowanie zdjęć na podstawie trasy GPX.

Zapisuje wyłącznie metadane GPS (przez exiftool). Dane obrazu nie są
ponownie kompresowane, a program po każdym zapisie sprawdza sumą
kontrolną, że pozostały identyczne co do bajta.
"""
import argparse
import bisect
import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

ROZSZERZENIA = {".jpg", ".jpeg"}


# ---------- GPX ----------

def _lokalna(tag):
    return tag.rsplit("}", 1)[-1]


def _czas_gpx(tekst):
    tekst = tekst.strip()
    if tekst.endswith(("Z", "z")):
        tekst = tekst[:-1] + "+00:00"
    czas = datetime.fromisoformat(tekst)
    if czas.tzinfo is None:          # GPX z definicji jest w UTC
        czas = czas.replace(tzinfo=timezone.utc)
    return czas.astimezone(timezone.utc)


def wczytaj_gpx(sciezki):
    """Zwraca posortowaną listę (czas_unix, szer, dł, wysokość|None)."""
    punkty = []
    for sciezka in sciezki:
        for _, el in ET.iterparse(sciezka):
            if _lokalna(el.tag) != "trkpt":
                continue
            czas = wys = None
            for dziecko in el:
                nazwa = _lokalna(dziecko.tag)
                if nazwa == "time" and dziecko.text:
                    czas = dziecko.text
                elif nazwa == "ele" and dziecko.text:
                    wys = dziecko.text
            if czas is not None:
                try:
                    punkty.append((
                        _czas_gpx(czas).timestamp(),
                        float(el.attrib["lat"]),
                        float(el.attrib["lon"]),
                        float(wys) if wys is not None else None,
                    ))
                except (ValueError, KeyError):
                    pass
            el.clear()
    punkty.sort(key=lambda p: p[0])
    return punkty


def _odleglosc_m(a, b):
    r = 6371000.0
    f1, f2 = math.radians(a[1]), math.radians(b[1])
    df, dl = f2 - f1, math.radians(b[2] - a[2])
    h = math.sin(df / 2) ** 2 + math.cos(f1) * math.cos(f2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def dopasuj(punkty, czasy, t, max_odstep):
    """Zwraca (szer, dł, wys, odstęp_s) albo (None, powód)."""
    i = bisect.bisect_left(czasy, t)
    if i == 0:
        przed, po = None, punkty[0]
    elif i == len(punkty):
        przed, po = punkty[-1], None
    else:
        przed, po = punkty[i - 1], punkty[i]

    if przed is None or po is None:
        p = po or przed
        odstep = abs(p[0] - t)
        if odstep > max_odstep:
            gdzie = "przed początkiem" if przed is None else "po końcu"
            return None, f"{gdzie} trasy o {_czas_trwania(odstep)}"
        return p[1], p[2], p[3], odstep

    odstep = min(t - przed[0], po[0] - t)
    # Dłuższa przerwa w zapisie (np. autopauza) jest w porządku,
    # jeśli w jej trakcie praktycznie nie zmieniono położenia.
    if odstep > max_odstep and _odleglosc_m(przed, po) > 100:
        return None, f"przerwa w trasie, najbliższy punkt {_czas_trwania(odstep)} dalej"
    rozpietosc = po[0] - przed[0]
    u = (t - przed[0]) / rozpietosc if rozpietosc > 0 else 0.0
    szer = przed[1] + (po[1] - przed[1]) * u
    dlug = przed[2] + (po[2] - przed[2]) * u
    if przed[3] is not None and po[3] is not None:
        wys = przed[3] + (po[3] - przed[3]) * u
    else:
        wys = przed[3] if przed[3] is not None else po[3]
    return szer, dlug, wys, odstep


def _czas_trwania(s):
    s = int(round(s))
    if s < 120:
        return f"{s} s"
    if s < 7200:
        return f"{s // 60} min"
    return f"{s // 3600} h {s % 3600 // 60} min"


# ---------- zdjęcia ----------

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


def _strefa(tekst):
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
            return czas.replace(tzinfo=_strefa(str(przesuniecie))), "z aparatu"
        except (ValueError, IndexError):
            pass
    return czas.astimezone(), "systemowa (brak w EXIF)"


# ---------- kontrola nienaruszenia obrazu ----------

def suma_obrazu(sciezka):
    """SHA-256 wszystkiego poza segmentami metadanych (APPn, COM).

    Obejmuje tablice kwantyzacji i Huffmana, nagłówki ramki oraz cały
    skompresowany strumień obrazu aż do końca pliku.
    """
    suma = hashlib.sha256()
    with open(sciezka, "rb") as f:
        if f.read(2) != b"\xff\xd8":
            raise ValueError("to nie jest plik JPEG")
        while True:
            naglowek = f.read(4)
            if len(naglowek) < 4 or naglowek[0] != 0xFF:
                raise ValueError("uszkodzona struktura JPEG")
            znacznik = naglowek[1]
            dlugosc = int.from_bytes(naglowek[2:4], "big")
            metadane = 0xE0 <= znacznik <= 0xEF or znacznik == 0xFE
            if znacznik == 0xDA:                    # początek danych obrazu
                suma.update(naglowek)
                while True:
                    blok = f.read(1 << 20)
                    if not blok:
                        return suma.hexdigest()
                    suma.update(blok)
            dane = f.read(dlugosc - 2)
            if not metadane:
                suma.update(naglowek)
                suma.update(dane)


def zapisz(sciezka, szer, dlug, wys, czas_utc, kopia):
    przed = suma_obrazu(sciezka)
    katalog = os.path.dirname(os.path.abspath(sciezka))
    fd, tymczasowy = tempfile.mkstemp(prefix=".gpxfoto-", suffix=".jpg", dir=katalog)
    os.close(fd)
    os.unlink(tymczasowy)            # exiftool -o wymaga, by plik nie istniał
    try:
        polecenie = [
            "exiftool", "-q", "-n", "-m",
            f"-GPSLatitude={abs(szer):.8f}", f"-GPSLatitudeRef={'N' if szer >= 0 else 'S'}",
            f"-GPSLongitude={abs(dlug):.8f}", f"-GPSLongitudeRef={'E' if dlug >= 0 else 'W'}",
            f"-GPSDateStamp={czas_utc:%Y:%m:%d}", f"-GPSTimeStamp={czas_utc:%H:%M:%S}",
            "-GPSMapDatum=WGS-84",
        ]
        if wys is not None:
            polecenie += [f"-GPSAltitude={abs(wys):.1f}", f"-GPSAltitudeRef={0 if wys >= 0 else 1}"]
        polecenie += ["-o", tymczasowy, "--", sciezka]
        proces = subprocess.run(polecenie, capture_output=True, text=True)
        if proces.returncode != 0 or not os.path.exists(tymczasowy):
            raise RuntimeError(proces.stderr.strip() or "exiftool nie zapisał pliku")
        if suma_obrazu(tymczasowy) != przed:
            raise RuntimeError("dane obrazu różnią się po zapisie — zmiana odrzucona")
        stat = os.stat(sciezka)
        shutil.copymode(sciezka, tymczasowy)
        os.utime(tymczasowy, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        if kopia:
            cel = os.path.join(katalog, "oryginaly")
            os.makedirs(cel, exist_ok=True)
            shutil.copy2(sciezka, os.path.join(cel, os.path.basename(sciezka)))
        os.replace(tymczasowy, sciezka)   # podmiana atomowa
    finally:
        if os.path.exists(tymczasowy):
            os.unlink(tymczasowy)


# ---------- program ----------

def main():
    ap = argparse.ArgumentParser(
        description="Dopisuje do zdjęć lokalizację z trasy GPX, nie zmieniając obrazu.")
    ap.add_argument("zdjecia", nargs="+", help="pliki JPG lub katalogi ze zdjęciami")
    ap.add_argument("-g", "--gpx", action="append", required=True,
                    help="plik GPX z trasą (można podać kilka razy)")
    ap.add_argument("--zapisz", action="store_true",
                    help="zapisz lokalizację do plików (bez tego tylko podgląd)")
    ap.add_argument("--korekta", type=float, default=0.0, metavar="S",
                    help="poprawka zegara aparatu w sekundach (dodawana do czasu zdjęcia)")
    ap.add_argument("--strefa", metavar="+GG:MM",
                    help="strefa czasowa aparatu; domyślnie brana z EXIF zdjęcia")
    ap.add_argument("--max-odstep", type=float, default=120.0, metavar="S",
                    help="największy dopuszczalny odstęp od punktu trasy (domyślnie 120 s)")
    ap.add_argument("--nadpisz", action="store_true",
                    help="zmień także zdjęcia, które już mają lokalizację")
    ap.add_argument("--kopia", action="store_true",
                    help="zachowaj oryginały w podkatalogu „oryginaly”")
    ap.add_argument("-r", "--rekurencyjnie", action="store_true",
                    help="szukaj zdjęć także w podkatalogach")
    a = ap.parse_args()

    if shutil.which("exiftool") is None:
        sys.exit("Brak programu exiftool. Na Fedorze: sudo dnf install perl-Image-ExifTool")
    try:
        strefa = _strefa(a.strefa) if a.strefa else None
    except (ValueError, IndexError):
        sys.exit("Strefę podaj w postaci +02:00 albo -05:00")

    punkty = wczytaj_gpx(a.gpx)
    if not punkty:
        sys.exit("W pliku GPX nie ma punktów trasy z czasem.")
    czasy = [p[0] for p in punkty]
    pocz = datetime.fromtimestamp(czasy[0]).astimezone()
    kon = datetime.fromtimestamp(czasy[-1]).astimezone()
    print(f"Trasa: {len(punkty)} punktów, {pocz:%Y-%m-%d %H:%M:%S} – {kon:%H:%M:%S} (czas lokalny komputera)")

    pliki = znajdz_zdjecia(a.zdjecia, a.rekurencyjnie)
    if not pliki:
        sys.exit("Nie znaleziono zdjęć JPG.")
    metadane = odczytaj_metadane(pliki)

    plan, pominiete = [], 0
    for meta in metadane:
        sciezka = meta["SourceFile"]
        nazwa = os.path.basename(sciezka)
        if "GPSLatitude" in meta and not a.nadpisz:
            print(f"  {nazwa:<16} pominięte: ma już lokalizację")
            pominiete += 1
            continue
        czas, zrodlo = czas_zdjecia(meta, strefa)
        if czas is None:
            print(f"  {nazwa:<16} pominięte: {zrodlo}")
            pominiete += 1
            continue
        czas += timedelta(seconds=a.korekta)
        wynik = dopasuj(punkty, czasy, czas.timestamp(), a.max_odstep)
        if wynik[0] is None:
            print(f"  {nazwa:<16} {czas:%H:%M:%S}  pominięte: {wynik[1]}")
            pominiete += 1
            continue
        szer, dlug, wys, odstep = wynik
        wys_txt = f"{wys:6.0f} m" if wys is not None else "       —"
        uwaga = "" if zrodlo == "z aparatu" else f"  [strefa {zrodlo}]"
        print(f"  {nazwa:<16} {czas:%H:%M:%S}  {szer:.6f}, {dlug:.6f} {wys_txt}{uwaga}")
        plan.append((sciezka, szer, dlug, wys, czas.astimezone(timezone.utc)))

    print(f"Dopasowano: {len(plan)}, pominięto: {pominiete}")
    if not a.zapisz:
        if plan:
            print("To był podgląd, nic nie zapisano. Dodaj --zapisz, żeby zapisać.")
        return

    zapisane = bledy = 0
    for sciezka, szer, dlug, wys, czas_utc in plan:
        try:
            zapisz(sciezka, szer, dlug, wys, czas_utc, a.kopia)
            zapisane += 1
        except (RuntimeError, ValueError, OSError) as e:
            bledy += 1
            print(f"  BŁĄD {os.path.basename(sciezka)}: {e} (plik bez zmian)")
    print(f"Zapisano: {zapisane}, błędy: {bledy}. Obraz w każdym zapisanym pliku sprawdzony: bez zmian.")
    if bledy:
        sys.exit(1)


if __name__ == "__main__":
    main()
