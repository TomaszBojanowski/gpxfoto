"""Wczytywanie tras GPX i wyznaczanie położenia w danej chwili."""
import bisect
import math
import xml.etree.ElementTree as ET
from datetime import datetime, timezone


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
