"""Polecenie terminalowe gpxfoto."""
import argparse
import os
import shutil
import sys
from datetime import datetime, timedelta, timezone

from gpxfoto.silnik.trasa import dopasuj, wczytaj_gpx
from gpxfoto.silnik.zapis import zapisz
from gpxfoto.silnik.zdjecia import (
    czas_zdjecia, odczytaj_metadane, strefa_z_tekstu, znajdz_zdjecia)


def main():
    ap = argparse.ArgumentParser(
        prog="gpxfoto",
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
        strefa = strefa_z_tekstu(a.strefa) if a.strefa else None
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
