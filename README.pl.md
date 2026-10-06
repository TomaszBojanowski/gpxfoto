# gpxfoto

[English](README.md)

gpxfoto dopisuje do zdjęć położenie na podstawie trasy GPX zapisanej
zegarkiem lub telefonem. Zapisuje wyłącznie metadane GPS i nigdy nie
zmienia obrazu.

- **Obraz pozostaje bez zmian.** Zapisywane są tylko metadane (za pomocą
  programu exiftool); obraz nie jest ponownie kodowany.
- **Każdy zapis jest sprawdzany.** Przed zapisem i po nim program liczy sumę
  SHA-256 wszystkiego poza segmentami metadanych. Jeśli sumy się różnią,
  zmiana zostaje odrzucona, a oryginalny plik pozostaje nietknięty.
- **Zapis jest atomowy.** Wynik trafia do pliku tymczasowego w tym samym
  katalogu i zastępuje oryginał dopiero po sprawdzeniu. Uprawnienia pliku
  i data modyfikacji zostają zachowane.
- **Domyślnie podgląd.** Bez opcji `--write` nic nie jest zmieniane.
- **Bez zgadywania.** Zdjęcie bez wiarygodnego punktu trasy zostaje bez
  położenia, a program podaje powód.

## Wymagania

- Python 3.11 lub nowszy
- [ExifTool](https://exiftool.org/) (w systemie Fedora: `sudo dnf install perl-Image-ExifTool`)
- do instalacji: `msgfmt` z pakietu GNU gettext (w systemie Fedora: `sudo dnf install gettext`)

## Instalacja

```
pip install --user .
```

## Użycie

```
gpxfoto ZDJĘCIE… -g PLIK [-g PLIK …] [opcje]
```

Podgląd tego, co zostałoby zapisane:

```
gpxfoto ~/Obrazy/2026-10-06 -g activity.gpx
```

Zapisanie położenia:

```
gpxfoto ~/Obrazy/2026-10-06 -g activity.gpx --write
```

| Opcja | Znaczenie |
|---|---|
| `-g`, `--gpx PLIK` | plik GPX z trasą; można podać wielokrotnie |
| `--write` | zapisuje położenie; bez tej opcji wyświetlany jest tylko podgląd |
| `--offset SEKUNDY` | poprawka zegara aparatu, dodawana do czasu wykonania zdjęcia |
| `--timezone +GG:MM` | strefa czasowa aparatu dla wszystkich zdjęć (domyślnie: odczytywana z danych EXIF każdego zdjęcia) |
| `--max-gap SEKUNDY` | największy dopuszczalny odstęp czasu między zdjęciem a najbliższym punktem trasy (domyślnie: 120 s) |
| `--overwrite` | zmienia także zdjęcia, które mają już zapisane położenie |
| `--backup` | zachowuje kopie oryginalnych plików w podkatalogu `originals` obok każdego zdjęcia |
| `-r`, `--recursive` | wyszukuje zdjęcia także w podkatalogach |

Strefa czasowa zdjęcia jest odczytywana z pola `OffsetTimeOriginal` (lub
`OffsetTime`), które zapisują aparaty takie jak Panasonic LUMIX S5II. Opcja
`--timezone` zastępuje ją dla wszystkich zdjęć. Jeśli nie ma żadnej z nich,
używana jest strefa czasowa komputera, o czym informuje lista zdjęć.

Zdjęcie zostaje pominięte, jeśli czas jego wykonania dzieli od najbliższego
punktu trasy więcej niż `--max-gap`: przed początkiem trasy, po jej
zakończeniu albo w przerwie w zapisie trasy. Zdjęcie wykonane w czasie
takiej przerwy otrzymuje jednak położenie, jeśli w trakcie przerwy zapisane
położenie zmieniło się o mniej niż 100 m.

Program jest po angielsku, z polskim tłumaczeniem; język wynika z ustawień
systemu.

## Rozwój

```
pip install -e .[test]
pytest
```

Własne zdjęcia i trasy można umieścić w katalogu `tests/prywatne/`. Git go
pomija; `tests/test_private.py` korzysta z tych plików, jeśli tam są.

### Tłumaczenia

Komunikaty są pisane po angielsku i tłumaczone za pomocą narzędzia
gettext; polskie tłumaczenie znajduje się w `po/pl.po`. Po zmianie
komunikatów należy zaktualizować szablon i tłumaczenia:

```
xgettext --files-from=po/POTFILES.in --from-code=UTF-8 --language=Python \
    --keyword=N_ --add-comments=Translators: --package-name=gpxfoto \
    --package-version=0.1.0 \
    --msgid-bugs-address=https://github.com/tomaszbojanowski/gpxfoto/issues \
    --output=po/gpxfoto.pot
msgmerge --update --backup=none po/pl.po po/gpxfoto.pot
```
