<p align="center">
  <img src="data/icons/hicolor/scalable/apps/io.github.tomaszbojanowski.Gpxfoto.svg" width="128" alt="Ikona programu gpxfoto">
</p>

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
| `--clock-photo PLIK` | zdjęcie dokładnego zegara, na przykład zegarka zapisującego trasę; razem z `--clock-time` służy do wyznaczenia poprawki zegara aparatu |
| `--clock-time CZAS` | czas widoczny na zegarze na tym zdjęciu, w formacie 24-godzinnym: `14:03:27`, `14:03:27+02:00` lub `2026-10-06T14:03:27+02:00` |
| `--max-gap SEKUNDY` | największy dopuszczalny odstęp czasu między zdjęciem a najbliższym punktem trasy (domyślnie: 120 s) |
| `--overwrite` | zmienia także zdjęcia, które mają już zapisane położenie |
| `--backup` | zachowuje kopie oryginalnych plików w podkatalogu `originals` obok każdego zdjęcia; istniejąca kopia nigdy nie jest zastępowana |
| `-r`, `--recursive` | wyszukuje zdjęcia także w podkatalogach |

Strefa czasowa zdjęcia jest odczytywana z pola `OffsetTimeOriginal` (lub
`OffsetTime`), które zapisują aparaty takie jak Panasonic LUMIX S5II. Opcja
`--timezone` zastępuje ją dla wszystkich zdjęć. Jeśli nie ma żadnej z nich,
używana jest strefa czasowa komputera, o czym informuje lista zdjęć.

### Poprawka zegara aparatu

Zegar aparatu spieszy się lub spóźnia, a czasy trasy pochodzą z GPS. Aby
ustalić, o ile myli się aparat, należy zrobić aparatem zdjęcie zegarka lub
innego dokładnego zegara, odczytać z niego czas i podać oba:

```
gpxfoto ~/Obrazy/2026-10-06 -g activity.gpx --clock-photo ~/Obrazy/2026-10-06/P1000123.JPG --clock-time 14:03:27
```

gpxfoto odejmuje czas wykonania tego zdjęcia od czasu na zegarze i dodaje
różnicę do czasu wykonania każdego zdjęcia, tak jak opcja `--offset`;
opcji `--offset` nie można wtedy podać. Podgląd pokazuje poprawkę,
odpowiadającą jej wartość `--offset` i oba odczyty.

Różnica 30 minut lub większa może oznaczać, że zegar i aparat pokazują czas
różnych stref czasowych, na przykład gdy aparatu nie przestawiono na czas
letni. gpxfoto nie potrafi tego rozstrzygnąć, więc prosi wtedy
o przesunięcie zegara względem UTC, na przykład `14:03:27+02:00`. Przy
różnicy większej niż dwie godziny trzeba podać także datę widoczną na
zegarze, na przykład `2026-10-06T14:03:27+02:00`. Czas podany bez sekund
oznacza środek minuty.

Zdjęcie zegara powinno pochodzić z tych samych dni co pozostałe zdjęcia:
aparat, który sam nie przestawia się na czas letni, po zmianie czasu myli
się o inną wartość.

Zdjęcie zostaje pominięte, jeśli czas jego wykonania dzieli od najbliższego
punktu trasy więcej niż `--max-gap`: przed początkiem trasy, po jej
zakończeniu albo w przerwie w zapisie trasy. Zdjęcie wykonane w czasie
takiej przerwy otrzymuje jednak położenie, jeśli w trakcie przerwy zapisane
położenie zmieniło się o mniej niż 100 m.

Z opcją `--backup` kopia każdego zdjęcia sprzed zapisu trafia do katalogu
`originals` obok niego. gpxfoto oznacza ten katalog plikiem `.gpxfoto`,
nigdy nie zmienia znajdujących się w nim kopii i pomija go przy
wyszukiwaniu z opcją `-r`. Istniejąca kopia musi zawierać ten sam obraz co
zdjęcie; jeśli w jej miejscu jest coś innego, zdjęcie nie zostaje zapisane.

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

Instalacja w trybie edytowalnym kompiluje tłumaczenia jednorazowo. Aby
zobaczyć zmiany w `po/pl.po` bez ponownej instalacji, należy skompilować
plik jeszcze raz:

```
msgfmt --check -o gpxfoto/locale/pl/LC_MESSAGES/gpxfoto.mo po/pl.po
```

## Licencja

gpxfoto jest wolnym oprogramowaniem: można je rozpowszechniać i modyfikować
na warunkach Powszechnej Licencji Publicznej GNU (GNU GPL), opublikowanej
przez Free Software Foundation, w wersji 3 lub (według uznania) dowolnej
późniejszej. Pełny tekst licencji znajduje się w pliku [LICENSE](LICENSE).
