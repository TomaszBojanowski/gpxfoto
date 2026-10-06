"""Zapis lokalizacji przez exiftool z kontrolą nienaruszenia obrazu."""
import hashlib
import os
import shutil
import subprocess
import tempfile


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
