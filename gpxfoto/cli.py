"""Command-line tool."""
import argparse
import os
import shutil
import sys
from datetime import datetime, timedelta, timezone

from gpxfoto.engine.photos import (
    TZ_MANUAL, TZ_SYSTEM, capture_time, find_photos, parse_utc_offset, read_metadata)
from gpxfoto.engine.track import load_gpx, locate
from gpxfoto.engine.writer import write_location

# Notes shown next to photos whose time zone did not come from the camera
TZ_NOTES = {
    TZ_MANUAL: "ręczna",
    TZ_SYSTEM: "systemowa (brak w EXIF)",
}


def main():
    parser = argparse.ArgumentParser(
        prog="gpxfoto",
        description="Dopisuje do zdjęć lokalizację z trasy GPX, nie zmieniając obrazu.")
    parser.add_argument("photos", nargs="+", metavar="zdjecia",
                        help="pliki JPG lub katalogi ze zdjęciami")
    parser.add_argument("-g", "--gpx", action="append", required=True,
                        help="plik GPX z trasą (można podać kilka razy)")
    parser.add_argument("--zapisz", dest="write", action="store_true",
                        help="zapisz lokalizację do plików (bez tego tylko podgląd)")
    parser.add_argument("--korekta", dest="offset", type=float, default=0.0, metavar="S",
                        help="poprawka zegara aparatu w sekundach (dodawana do czasu zdjęcia)")
    parser.add_argument("--strefa", dest="timezone", metavar="+GG:MM",
                        help="strefa czasowa aparatu; domyślnie brana z EXIF zdjęcia")
    parser.add_argument("--max-odstep", dest="max_gap", type=float, default=120.0, metavar="S",
                        help="największy dopuszczalny odstęp od punktu trasy (domyślnie 120 s)")
    parser.add_argument("--nadpisz", dest="overwrite", action="store_true",
                        help="zmień także zdjęcia, które już mają lokalizację")
    parser.add_argument("--kopia", dest="backup", action="store_true",
                        help="zachowaj oryginały w podkatalogu „oryginaly”")
    parser.add_argument("-r", "--rekurencyjnie", dest="recursive", action="store_true",
                        help="szukaj zdjęć także w podkatalogach")
    args = parser.parse_args()

    if shutil.which("exiftool") is None:
        sys.exit("Brak programu exiftool. Na Fedorze: sudo dnf install perl-Image-ExifTool")
    try:
        manual_tz = parse_utc_offset(args.timezone) if args.timezone else None
    except (ValueError, IndexError):
        sys.exit("Strefę podaj w postaci +02:00 albo -05:00")

    points = load_gpx(args.gpx)
    if not points:
        sys.exit("W pliku GPX nie ma punktów trasy z czasem.")
    times = [p[0] for p in points]
    start = datetime.fromtimestamp(times[0]).astimezone()
    end = datetime.fromtimestamp(times[-1]).astimezone()
    print(f"Trasa: {len(points)} punktów, {start:%Y-%m-%d %H:%M:%S} – {end:%H:%M:%S} (czas lokalny komputera)")

    try:
        files = find_photos(args.photos, args.recursive)
    except FileNotFoundError as e:
        sys.exit(str(e))
    if not files:
        sys.exit("Nie znaleziono zdjęć JPG.")
    try:
        metadata = read_metadata(files)
    except RuntimeError as e:
        sys.exit(str(e))

    plan, skipped = [], 0
    for meta in metadata:
        path = meta["SourceFile"]
        name = os.path.basename(path)
        if "GPSLatitude" in meta and not args.overwrite:
            print(f"  {name:<16} pominięte: ma już lokalizację")
            skipped += 1
            continue
        taken, detail = capture_time(meta, manual_tz)
        if taken is None:
            print(f"  {name:<16} pominięte: {detail}")
            skipped += 1
            continue
        taken += timedelta(seconds=args.offset)
        result = locate(points, times, taken.timestamp(), args.max_gap)
        if result[0] is None:
            print(f"  {name:<16} {taken:%H:%M:%S}  pominięte: {result[1]}")
            skipped += 1
            continue
        lat, lon, ele, gap = result
        ele_text = f"{ele:6.0f} m" if ele is not None else "       —"
        note = f"  [strefa {TZ_NOTES[detail]}]" if detail in TZ_NOTES else ""
        print(f"  {name:<16} {taken:%H:%M:%S}  {lat:.6f}, {lon:.6f} {ele_text}{note}")
        plan.append((path, lat, lon, ele, taken.astimezone(timezone.utc)))

    print(f"Dopasowano: {len(plan)}, pominięto: {skipped}")
    if not args.write:
        if plan:
            print("To był podgląd, nic nie zapisano. Dodaj --zapisz, żeby zapisać.")
        return

    written = errors = 0
    for path, lat, lon, ele, time_utc in plan:
        try:
            write_location(path, lat, lon, ele, time_utc, args.backup)
            written += 1
        except (RuntimeError, ValueError, OSError) as e:
            errors += 1
            print(f"  BŁĄD {os.path.basename(path)}: {e} (plik bez zmian)")
    print(f"Zapisano: {written}, błędy: {errors}. Obraz w każdym zapisanym pliku sprawdzony: bez zmian.")
    if errors:
        sys.exit(1)


if __name__ == "__main__":
    main()
