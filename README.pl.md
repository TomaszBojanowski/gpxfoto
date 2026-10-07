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
gpxfoto ZDJĘCIE… -g TRASA [-g TRASA …] [opcje]
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
| `-g`, `--gpx TRASA` | plik GPX z trasą lub katalog z plikami GPX; można podać wielokrotnie |
| `--write` | zapisuje położenie; bez tej opcji wyświetlany jest tylko podgląd |
| `--offset SEKUNDY` | poprawka zegara aparatu, dodawana do czasu wykonania zdjęcia |
| `--timezone +GG:MM` | strefa czasowa aparatu dla wszystkich zdjęć (domyślnie: odczytywana z danych EXIF każdego zdjęcia) |
| `--clock-photo PLIK` | zdjęcie dokładnego zegara, na przykład zegarka zapisującego trasę; razem z `--clock-time` służy do wyznaczenia poprawki zegara aparatu |
| `--clock-time CZAS` | czas widoczny na zegarze na tym zdjęciu, w formacie 24-godzinnym: `14:03:27`, `14:03:27+02:00` lub `2026-10-06T14:03:27+02:00` |
| `--max-gap SEKUNDY` | największy dopuszczalny odstęp czasu między zdjęciem a najbliższym punktem trasy (domyślnie: 120 s) |
| `--no-stops` | nie wyszukuje postojów; każde zdjęcie otrzymuje położenie z trasy w chwili wykonania |
| `--overwrite` | zmienia także zdjęcia, które mają już zapisane położenie |
| `--travel-direction` | dopisuje także kierunek ruchu z trasy (EXIF `GPSTrack`); zob. niżej |
| `--backup` | zachowuje kopie oryginalnych plików w podkatalogu `originals` obok każdego zdjęcia; istniejąca kopia nigdy nie jest zastępowana |
| `-r`, `--recursive` | wyszukuje zdjęcia i trasy także w podkatalogach |

Strefa czasowa zdjęcia jest odczytywana z pola `OffsetTimeOriginal` (lub
`OffsetTime`), które zapisują aparaty takie jak Panasonic LUMIX S5II. Opcja
`--timezone` zastępuje ją dla wszystkich zdjęć. Jeśli nie ma żadnej z nich,
używana jest strefa czasowa komputera, o czym informuje lista zdjęć.

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

### Kontrola strefy czasowej zdjęć z S5II

Panasonic LUMIX S5II zapisuje czas wykonania zdjęcia także w UTC, w danych
producenta (`Panasonic:TimeStamp`). Dla zdjęć z tego aparatu gpxfoto
porównuje ten czas z czasem wykonania, którego używa, przed poprawką
zegara; różnica, którą poprawka wyrównuje, nie jest zgłaszana, a zdjęcia,
które mają już położenie i nie są nadpisywane, nie są sprawdzane. Jeśli
czasy różnią się o więcej niż dwie minuty, wiersz zdjęcia otrzymuje
uwagę, a ostrzeżenie pod listą podaje możliwą przyczynę: błędną opcję
`--timezone`, strefę czasową komputera użytą dla zdjęć bez strefy w EXIF
albo czas wykonania zmieniony w EXIF przez inny program. Jeśli różnica
odpowiada strefie czasowej używanej na świecie, ostrzeżenie podaje wartość
`--timezone`, przy której oba czasy byłyby zgodne, chyba że rozstroiłaby
ona zdjęcia, których czasy już się zgadzają. Kontrola nigdy nie
zmienia czasu ani położenia. Nie wykryje błędnego ustawienia strefy
czasowej w samym aparacie, bo aparat wylicza oba czasy z tego ustawienia.

### Kilka tras

Pliki GPX podane w opcji `-g` tworzą razem jedną trasę, a wiersz trasy
podaje nazwę pliku, jeśli jest tylko jeden. Przy kilku plikach wiersz
każdego zdjęcia podaje plik, z którego pochodzi jego położenie.

W opcji `-g` można też podać katalog, na przykład ten, w którym
przechowywane są wszystkie aktywności. Każdy plik `.gpx` w tym katalogu,
a z opcją `-r` także w jego podkatalogach, jest osobną trasą, a każde
zdjęcie otrzymuje trasę obejmującą czas jego wykonania; położenie nigdy nie
powstaje z dwóch różnych plików. Jeśli czas zdjęcia obejmuje kilka tras,
wygrywa ta, której zapisane punkty leżą najbliżej tego czasu, potem plik
podany z nazwy, a potem ten, który zapisuje punkty częściej. Jeśli dwie
trasy z katalogu, których punkty leżą równie blisko czasu zdjęcia,
umieszczają zdjęcie w miejscach odległych o ponad 200 m,
zdjęcie zostaje pominięte, podobnie jak zdjęcie z czasu pliku, którego nie
można odczytać. Przy zdjęciu, którego czasu nie obejmuje żadna trasa,
podana jest najbliższa trasa w ciągu doby. Aby przy wielu plikach
pozostać szybkim, gpxfoto najpierw odczytuje z każdego pliku tylko czasy,
a w całości czyta jedynie pliki potrzebne dla zdjęć.

### Postoje

Zdjęcie zrobione podczas postoju otrzymuje stabilniejsze położenie.
gpxfoto wyszukuje na trasie postoje: odcinki trwające co najmniej około
pół minuty, na których trasa przesuwa się wolniej niż 0,2 m/s, a jeśli ma
wysokość z barometru, także wznosi się lub opada wolniej niż 0,03 m/s,
dzięki czemu powolne, strome podejście nie jest postojem. Postojem jest też
przerwa w zapisie trasy, której oba końce dzieli najwyżej 10 m. Zdjęcie
wykonane w czasie postoju, gdy trasa znajduje się najwyżej 20 m od niego,
a przy wysokości z barometru także najwyżej 5 m wyżej lub niżej, otrzymuje
położenie postoju, czyli medianę jego punktów, a w jego wierszu podane są
godziny postoju. Położenie
pozostałych zdjęć się nie zmienia. Przerwy krótsze niż około minuty zwykle
nie są postojami.

Pod listą zdjęć wiersz „Na postojach: N z M dopasowanych zdjęć” podaje, ile
zdjęć wykonano podczas postojów. Zdjęcia robi się przeważnie na postoju,
więc przy źle ustawionym zegarze aparatu ta liczba jest zwykle mniejsza.
Opcja `--no-stops` wyłącza postoje.

### Ostrzeżenia

W podglądzie gpxfoto wskazuje oznaki, że zegar aparatu jest źle ustawiony.
Sam niczego nie zmienia.

- **Przesunięcie o pełne godziny.** Jeśli po przesunięciu czasu zdjęć o pół
  godziny lub o pełne godziny (do 12) wyraźnie więcej zdjęć wypada na
  postojach, i to na co najmniej czterech różnych, gpxfoto proponuje takie
  przesunięcie wraz z opcją, która je stosuje: `--offset`; także
  `--timezone`, gdy nie podano poprawki, wszystkie zdjęcia mają tę samą
  strefę czasową i żadne nie pochodzi z aparatu zapisującego czas UTC, jak
  S5II; albo `--clock-time` z innym przesunięciem względem UTC, gdy
  poprawka pochodzi ze zdjęcia zegara. Różnica dokładnie jednej godziny
  zwykle oznacza, że w aparacie nie przestawiono czasu na letni lub zimowy.
- **Zdjęcia w ruchu.** Jeśli większość zdjęć wypada w chwilach, gdy według
  trasy poruszano się pełnym tempem, a nie na postojach ani przy
  zwalnianiu, zegar aparatu może się mylić o kilka minut. Kto robi
  większość zdjęć, nie zatrzymując się, może dostać to ostrzeżenie także
  przy dobrze ustawionym zegarze.
- **Nieprawdopodobne przeskoki.** Zdjęcia zrobione w odstępie krótszym niż
  minuta, ale umieszczone dalej od siebie, niż cokolwiek mogłoby w tym
  czasie przebyć (100 m/s, na szybszej trasie więcej), zwykle mają w EXIF
  różne strefy czasowe albo pochodzą z plików GPX różnych wycieczek
  nagranych w tym samym czasie.

Dwa pierwsze ostrzeżenia wymagają postojów i prędkości jednej trasy: nie
pojawiają się, gdy dopasowane zdjęcia pochodzą z kilku tras, gdy zdjęcia
pominięto z powodu innej trasy lub pliku, którego nie da się odczytać, ani
z opcją `--no-stops`. Gdy żadna trasa z katalogu nie obejmuje zdjęć,
sprawdzana jest najbliższa. Progi dobrano na prawdziwej wycieczce; przy dobrze ustawionym
zegarze nie dały żadnego ostrzeżenia w 900 modelowanych zestawach zdjęć
zrobionych głównie na postojach i krótkich przystankach.

### Kierunek ruchu

Z opcją `--travel-direction` dopasowane zdjęcie otrzymuje także kierunek,
w którym trasa przechodziła przez jego miejsce, w pełnych stopniach od
północy geograficznej, zapisany w polu EXIF `GPSTrack` z `GPSTrackRef`
równym T. To kierunek ruchu, a nie kierunek, w którym skierowany był
aparat, bo tego gpxfoto nie może wiedzieć; pole `GPSImgDirection` nigdy nie
jest zapisywane. Zdjęcie dostaje kierunek tylko tam, gdzie trasa wyraźnie
przechodzi przez jego miejsce: w ciągu minuty przed zdjęciem i po nim
oddala się o 20 m, a droga między tymi dwoma punktami jest najwyżej o 20%
dłuższa od linii prostej. Zdjęcia zrobione na postoju, na ostrym zakręcie,
na serpentynach lub w przerwie w zapisie trasy nie dostają kierunku,
podobnie jak zdjęcia między punktami dwóch plików nagranych w tym samym
czasie, a podgląd podaje przyczynę.
Z tą opcją starszy kierunek ruchu w zapisywanym zdjęciu, także w XMP,
zostaje usunięty, żeby nie uchodził za wyznaczony z trasy. Na wycieczce
autora kierunek dostało 84% zdjęć zrobionych w marszu.

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
