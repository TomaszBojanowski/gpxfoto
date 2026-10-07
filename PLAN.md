# gpxfoto — plan projektu

Program do geotagowania zdjęć z trasy GPX. Nazwa: **gpxfoto** (siostrzany projekt gpxfilm). Identyfikator aplikacji: `io.github.tomaszbojanowski.Gpxfoto`. Ikona jest gotowa w `data/icons/` — nie zmieniać treści plików; instaluje ją system budowania w etapie 4.

## Cel

Program dopisuje do zdjęć lokalizację na podstawie trasy zapisanej zegarkiem lub telefonem. Zastępuje zawodne geotagowanie przez aplikację Lumix na iPhonie.

Użytkownik: jedna osoba, Fedora z GNOME, aparat Panasonic LUMIX S5II (DC-S5M2), zdjęcia w JPG oraz RAW (RW2), trasy z Garmina (eksport GPX z Garmin Connect) oraz z innych źródeł.

## Wymagania bezwzględne

1. **Obraz nie może się zmienić.** Program zapisuje wyłącznie metadane. Żadnego ponownego kodowania JPEG.
2. **Każdy zapis jest weryfikowany.** Przed zapisem i po nim liczona jest suma SHA-256 wszystkiego poza segmentami metadanych (APPn, COM). Różnica oznacza odrzucenie zmiany; oryginał zostaje nietknięty.
3. **Zapis jest atomowy.** Wynik trafia do pliku tymczasowego w tym samym katalogu, po weryfikacji następuje `os.replace`. Zachowane zostają uprawnienia i data modyfikacji pliku.
4. **Domyślnie podgląd.** Bez wyraźnego polecenia zapisu program niczego nie zmienia.
5. **Nie zgadujemy.** Zdjęcie bez wiarygodnego punktu trasy zostaje bez lokalizacji, z podanym powodem.

## Język

- **Program jest po angielsku, z polskim tłumaczeniem.** Wszystkie teksty widoczne dla użytkownika (terminal i interfejs w przeglądarce) są w kodzie po angielsku i przechodzą przez gettext; polskie tłumaczenie w `po/pl.po` jest kompletne w każdym wydaniu.
- Nazwy opcji w terminalu są angielskie i nie podlegają tłumaczeniu (`--write`, `--backup`, `--offset`, `--timezone`, `--max-gap`, `--overwrite`, `--recursive`); tłumaczone są opisy opcji i komunikaty.
- Kod, nazwy modułów, funkcji i zmiennych, komentarze oraz opisy commitów po angielsku.
- `README.md` po angielsku, `README.pl.md` po polsku; opis i metainfo aplikacji w obu językach.
- Polskie tłumaczenie zgodne z konwencjami polskiego zespołu tłumaczy GNOME (formy bezosobowe, polskie cudzysłowy „”, wielokropek jako jeden znak, poprawne formy liczby mnogiej). Autor sprawdza je osobiście przed każdym wydaniem.
- Liczby, daty i jednostki formatowane według ustawień regionalnych systemu.
- Obecny `gpxfoto.py` ma polskie komunikaty, opcje i nazwy w kodzie — przełożenie na angielski jest częścią etapu 1.

## Szybkość i płynność (obowiązuje we wszystkich etapach)

Interfejs ma reagować natychmiast, niezależnie od tego, co dzieje się w tle.

- **Wątek interfejsu tylko rysuje i obsługuje zdarzenia.** Żadnego czytania plików, uruchamiania exiftoola, parsowania tras ani dekodowania obrazów w wątku głównym. Dotyczy to zarówno strony w przeglądarce (nic ciężkiego w głównym wątku strony), jak i serwera lokalnego: każde żądanie, które może trwać dłużej, jest zadaniem w tle, a postęp i wyniki płyną do strony strumieniem zdarzeń (SSE). Serwer obsługuje żądania wielowątkowo, więc długi zapis nie blokuje mapy ani miniatur.
- **Wszystko, co trwa, da się przerwać** (przycisk „Anuluj”) i pokazuje postęp. Przerwanie zapisu zostawia każdy plik w stanie spójnym: albo stary, albo nowy, nigdy połowiczny.
- **Wyniki napływają stopniowo**: zdjęcia pojawiają się na liście i mapie w miarę wczytywania, nie dopiero po zakończeniu całości.
- **Miniatury** brane z miniatury osadzonej w EXIF (bez dekodowania pełnego zdjęcia 24 Mpx), wczytywane leniwie dla widocznych elementów i trzymane w pamięci podręcznej o ograniczonym rozmiarze.
- **Lista zdjęć** na widżetach z recyklingiem wierszy (`Gtk.GridView`/`Gtk.ListView`), żeby tysiące pozycji nie tworzyły tysięcy widżetów.
- **Trasa na mapie upraszczana** do poziomu przybliżenia (np. algorytm Douglasa–Peuckera); pełna dokładność zostaje do obliczeń.
- **Suwak czasu** przelicza położenia w pamięci (wyszukiwanie binarne), bez dotykania dysku; przy szybkim przeciąganiu pomijane są pośrednie przeliczenia.
- **exiftool** działa jako jeden proces w trybie `-stay_open`; odczyt metadanych wsadowo, zapis kilku plików równolegle (liczba wątków do dobrania pomiarem).
- **Błąd w tle nie zawiesza ani nie zamyka programu**: awaria lub zawieszenie exiftoola (limit czasu), uszkodzony plik, odłączony dysk — komunikat dla użytkownika, pozostałe zdjęcia przetwarzane dalej.
- **Start programu** poniżej 1 s do otwarcia strony; cięższe moduły ładowane dopiero przy pierwszym użyciu.

Mierzalne cele na komputerze autora (do sprawdzania testem wydajności przy każdym etapie):

| Operacja | Cel |
|---|---|
| Wczytanie trasy 20 000 punktów | poniżej 0,5 s |
| Odczyt metadanych 1000 zdjęć | poniżej 5 s, lista wypełnia się na bieżąco |
| Przeliczenie dopasowania 1000 zdjęć po ruchu suwaka | poniżej 16 ms |
| Najdłuższa blokada wątku interfejsu | poniżej 50 ms |
| Zapis 1000 zdjęć | ograniczony szybkością dysku, interfejs płynny przez cały czas |

## Stan obecny

W repozytorium jest `gpxfoto.py` — działający silnik uruchamiany z terminala (Python 3, zapis przez `exiftool`). Potrafi:

- czytać GPX z dowolnego źródła (dowolna przestrzeń nazw, punkty `trkpt` z czasem), także kilka plików naraz,
- czytać czas zdjęcia z `DateTimeOriginal` + `SubSecTimeOriginal` + `OffsetTimeOriginal`,
- interpolować położenie i wysokość między sąsiednimi punktami trasy,
- pomijać zdjęcia poza trasą lub w przerwie zapisu dłuższej niż `--max-odstep` (120 s; docelowo `--max-gap`), chyba że w trakcie przerwy położenie zmieniło się o mniej niż 100 m,
- pomijać zdjęcia, które już mają lokalizację (chyba że `--nadpisz`),
- zapisywać: GPSLatitude/Ref, GPSLongitude/Ref, GPSAltitude/Ref, GPSDateStamp, GPSTimeStamp (UTC), GPSMapDatum,
- spełnić wymagania 1–5; opcje (jeszcze po polsku) `--zapisz`, `--kopia`, `--korekta`, `--strefa`, `-r`.

## Ustalenia z prawdziwych plików

- S5II zapisuje `OffsetTimeOriginal` (np. `+02:00`), więc strefa czasowa jest znana z samego zdjęcia. Jest też `Panasonic:TimeStamp` w UTC — można go użyć do kontroli krzyżowej.
- GPX z Garmin Connect ma punkt co sekundę, czas w UTC z sufiksem `Z`, wysokość w `ele`.
- Po zapisie przez exiftool plik jest o ok. 14 kB mniejszy: znika puste wypełnienie w bloku metadanych. Zmieniają się tylko wskaźniki `IFD1:ThumbnailOffset` i `MPImage2:MPImageStart`; pozostałe 302 pola, dane producenta, miniatura i podgląd MPF są zachowane, a zdekodowane piksele identyczne.
- exiftool zgłasza dla tych plików trzy drobne ostrzeżenia (`ClearRetouchValue`, `PanasonicTitle`, `PanasonicTitle2`). Są obecne już w oryginałach prosto z aparatu — to nie błąd programu.

## Technologia

- Python 3, w silniku tylko biblioteka standardowa; `exiftool` jako jedyne narzędzie zapisujące metadane.
- **Interfejs główny działa w przeglądarce** i jest taki sam na Linuksie i macOS: `gpxfoto --ui` uruchamia mały serwer lokalny i otwiera stronę.
  - Serwer: biblioteka standardowa Pythona (`http.server`, wielowątkowy), bez dodatkowych zależności.
  - Strona: zwykły HTML, CSS i JavaScript, bez etapu budowania i bez frameworka.
  - Mapa: biblioteka dołączona do repozytorium, nie z CDN (propozycja: MapLibre GL JS; wybór i licencję potwierdzić z autorem). Poza kafelkami mapy program nie łączy się z internetem.
- Dystrybucja: `pip`/`pipx` na Linuksie i macOS; wymagany `exiftool` w systemie.
- Okno GTK4/libadwaita i Flatpak są odłożone na później (etap 7) jako drugi interfejs do tego samego silnika.
- Tłumaczenia przez gettext (szczegóły w sekcji „Język”).

## Układ repozytorium (docelowy)

```
gpxfoto/
  engine/        # logika bez zależności od interfejsu
    track.py     # wczytywanie GPX/FIT, interpolacja, postoje
    photos.py    # odczyt metadanych, czas zdjęcia
    writer.py    # exiftool, suma kontrolna, zapis atomowy, cofanie
    clock.py     # wyznaczanie poprawki zegara aparatu
  cli.py         # polecenie terminalowe
  server/        # serwer lokalny i API dla strony (etap 4)
  web/           # strona: HTML, CSS, JS, biblioteka mapy (etap 4)
tests/
po/
```

Silnik nie importuje niczego z `server/` ani `web/`. Serwer wywołuje silnik wyłącznie w wątkach roboczych. Strona rozmawia z serwerem tylko przez opisane API, żeby przyszłe okno GTK mogło użyć tego samego silnika bez zmian.

## Etapy

Każdy etap kończy się działającym programem i przechodzącymi testami. Jedna funkcja na raz, każda w osobnym commicie.

### Etap 1 — porządek i testy

- Podział `gpxfoto.py` na moduły jak wyżej, bez zmiany zachowania.
- Przełożenie kodu, opcji i komunikatów na angielski; gettext, szablon `po/gpxfoto.pot` i kompletne `po/pl.po`.
- `pyproject.toml`, polecenie `gpxfoto` po instalacji.
- Testy (`pytest`): parser GPX (Garmin, plik bez przestrzeni nazw, czas z ułamkami i z przesunięciem strefy), interpolacja, przerwy w trasie, suma kontrolna obrazu, zapis atomowy, odrzucenie zmiany przy uszkodzonym wyniku.
- Pliki testowe JPEG generowane w testach lub małe, sztuczne. **Prywatnych zdjęć i tras autora nie dodawać do repozytorium** (`.gitignore` na `tests/prywatne/`).
- Kryterium: `pytest` przechodzi; wynik na czterech zdjęciach wzorcowych taki sam jak przed podziałem.

### Etap 2 — trafność

- **Poprawka zegara ze zdjęcia wzorcowego**: użytkownik wskazuje zdjęcie i podaje godzinę widoczną na nim (zdjęcie ekranu zegarka); program wylicza poprawkę i stosuje ją do całej serii.
- **Kontrola krzyżowa** `DateTimeOriginal` + przesunięcie z `Panasonic:TimeStamp`; rozbieżność zgłaszana jako ostrzeżenie.
- **Postoje**: wykrywanie odcinków bez ruchu; zdjęcie wykonane blisko postoju jest do niego przypinane. W podglądzie informacja, ile zdjęć wypada na postoje (wskaźnik, czy poprawka zegara jest dobra).
- **Samo dobieranie trasy**: wskazany katalog z trasami, każde zdjęcie dopasowane do pliku obejmującego jego czas.
- **Kierunek marszu** (GPSImgDirection / GPSTrack) — tylko jako opcja, domyślnie wyłączona; to kierunek ruchu, nie obiektywu.
- Kryterium: testy dla każdej funkcji; podgląd pokazuje zastosowaną poprawkę i źródło trasy dla każdego zdjęcia.

### Etap 2b — pliki RAW

- Obsługa **RW2** (Lumix) jako formatu podstawowego; inne formaty RAW, które exiftool potrafi zapisywać (DNG, CR2/CR3, NEF, ARW, RAF, ORF), dopuszczone, ale oznaczone jako nietestowane, dopóki nie ma pliku wzorcowego.
- **Dwa sposoby zapisu do wyboru**:
  - do samego pliku RAW (domyślnie) — przez exiftool, z weryfikacją jak niżej;
  - do pliku towarzyszącego `.xmp` obok zdjęcia — plik RAW pozostaje nietknięty co do bajta; nazwa pliku zgodna z konwencją digiKama i darktable (sprawdzić obie i dać wybór, jeśli się różnią). Istniejący plik `.xmp` jest uzupełniany, nie nadpisywany.
- **Weryfikacja dla RAW**: suma SHA-256 danych matrycy przed zapisem i po nim (blok surowych danych zlokalizowany ze struktury TIFF pliku, niezależnie od exiftoola). Dodatkowo porównanie wszystkich pozostałych pól metadanych przed i po; dozwolone są wyłącznie zmiany pól GPS i wewnętrznych wskaźników położenia. Jeśli dla danego formatu nie umiemy zlokalizować danych matrycy, zapis do pliku jest niedostępny i zostaje tylko `.xmp`.
- **Pary RAW+JPG** (ta sama nazwa, różne rozszerzenia) traktowane jako jedno zdjęcie: jedna pozycja na liście i mapie, identyczna lokalizacja w obu plikach.
- Miniatury dla RAW z podglądu JPEG osadzonego w pliku, bez wywoływania RAW.
- Przed implementacją: poprosić autora o przykładowy plik RW2 prosto z aparatu (oraz parę RAW+JPG) i na nim potwierdzić, że exiftool zachowuje dane producenta i podgląd. Wynik przedstawić autorowi.
- Kryterium: testy weryfikacji dla RW2; plik po zapisie otwiera się w digiKamie i darktable z poprawną lokalizacją i bez ostrzeżeń, których nie było w oryginale.

### Etap 3 — wygoda i bezpieczeństwo

- **Pliki FIT** prosto z zegarka (wybór biblioteki do potwierdzenia z autorem).
- **Cofanie**: polecenie usuwające dopisane pola GPS, z tą samą weryfikacją sumy obrazu.
- **Raport** po zapisie (plik tekstowy lub JSON obok zdjęć): plik, czas, współrzędne, źródło trasy, poprawka.
- **Szybkość**: jeden proces exiftool w trybie `-stay_open` zamiast uruchamiania dla każdego zdjęcia.
- **Nazwy miejsc** bez internetu (kraj, region, miejscowość do pól IPTC/XMP) z wbudowanej bazy miejscowości; sprawdzić licencję danych.
- **Filmy MP4/MOV** — najpierw zbadać, jak S5II zapisuje czas w filmach, i przedstawić wynik autorowi przed implementacją.
- Kryterium: 500 zdjęć zapisanych w czasie ograniczonym głównie kopiowaniem plików; cofnięcie przywraca metadane GPS do stanu sprzed zapisu.

### Etap 4 — interfejs w przeglądarce

- `gpxfoto --ui` uruchamia serwer lokalny na wolnym porcie i otwiera stronę w domyślnej przeglądarce. Zamknięcie karty lub Ctrl+C kończy program.
- **Wybór plików**: przeglądarka folderów po stronie serwera (strona nie zna ścieżek na dysku) oraz pole do wklejenia ścieżki. Plik trasy można też przeciągnąć na stronę. Zdjęcia są zawsze wskazywane ścieżką i zapisywane w miejscu, nigdy nie są przesyłane ani kopiowane przez przeglądarkę.
- Lista zdjęć z miniaturami i stanem (dopasowane / pominięte z powodem).
- Mapa ze śladem trasy i znacznikami zdjęć.
- Suwak poprawki czasu z podglądem na żywo: znaczniki przesuwają się po trasie.
- Ręczne przesunięcie pojedynczego zdjęcia na mapie.
- Zapis z paskiem postępu i możliwością przerwania; podsumowanie na końcu.
- **Interfejs nigdy się nie zawiesza**: wczytywanie tras, odczyt metadanych, miniatury i zapis to zadania w tle po stronie serwera; strona dostaje wyniki strumieniem zdarzeń. Przewidzieć to w architekturze od początku.
- **Bezpieczeństwo serwera lokalnego**:
  - nasłuch wyłącznie na `127.0.0.1`;
  - losowy token w adresie przy starcie, wymagany przy każdym żądaniu;
  - sprawdzanie nagłówków `Host` i `Origin` (ochrona przed innymi stronami otwartymi w przeglądarce);
  - serwer czyta i zapisuje tylko w folderach wskazanych przez użytkownika, z ochroną przed wyjściem poza nie;
  - żadnych zewnętrznych skryptów, czcionek ani statystyk.
- Teksty strony tłumaczone z tych samych katalogów gettext co terminal (serwer podaje je stronie); polskie tłumaczenie kompletne.
- Tryb jasny i ciemny według ustawień systemu; układ działa także na wąskim ekranie.
- Skrót do uruchamiania: plik `.desktop` z ikoną na Linuksie; na macOS sposób uruchamiania bez Terminala do zaproponowania autorowi.
- Testy: API serwera testowane bez przeglądarki; testy bezpieczeństwa (brak tokenu, zły `Host`, ścieżka poza wskazanym folderem).
- Kryterium: działa na Fedorze (Firefox) i macOS (Safari); przy 1000 zdjęć strona pozostaje płynna podczas wczytywania i zapisu.

### Etap 5 — wygląd i dopracowanie interfejsu

Program ma być ładny i przyjemny w użyciu, z własnym charakterem. Mapa jest głównym elementem strony, reszta to panele nad nią.

- **Mapa na całe okno**, nad nią półprzezroczyste panele (pasek narzędzi, lista zdjęć, suwak czasu).
- **Zdjęcia jako okrągłe miniatury na trasie**; przy oddaleniu łączą się w grupy z licznikiem, przy zbliżeniu rozdzielają się z animacją.
- **Płynny suwak czasu**: podczas przeciągania miniatury suną po trasie, a nie przeskakują.
- **Pasek zdjęć na dole**: kliknięcie miniatury płynnie przenosi mapę w miejsce zdjęcia; kliknięcie znacznika na mapie przewija pasek.
- **Podgląd po najechaniu**: większe zdjęcie z godziną, wysokością i nazwą miejsca.
- **Profil wysokości** pod mapą ze znacznikami zdjęć; najechanie na profil pokazuje punkt na mapie.
- **Trasa kolorowana** według wysokości lub prędkości (do wyboru).
- **Przełącznik stylu mapy**: zwykła i topograficzna (górska). Sprawdzić warunki użycia każdego źródła kafelków i dodać wymagane podpisy; nie dodawać źródeł bez jasnej licencji.
- **Animacja zapisu**: znaczniki po kolei zmieniają kolor na zielony; na końcu powiadomienie z przyciskiem „Cofnij”.
- **Karta podsumowania** po zapisie: liczba zdjęć, długość trasy, przewyższenie, odwiedzone miejscowości.
- **Ekran powitalny** z zachętą do przeciągnięcia plików i wyraźnym podświetleniem strefy upuszczania.
- Animacje w CSS i przez API mapy; respektować systemowe ustawienie ograniczenia animacji (`prefers-reduced-motion`).
- Kryterium: animacje płynne przy 1000 zdjęć; żaden efekt nie blokuje interfejsu ani nie spowalnia zapisu.

Podstawowe elementy z tej listy (mapa na całe okno, miniatury na trasie, pasek zdjęć) uwzględnić już w układzie strony w etapie 4, żeby nie przebudowywać go później.

### Etap 6 — praca na serwerze domowym (opcjonalnie)

- Uruchamianie na NAS-ie w kontenerze, z dostępem z innych urządzeń w sieci domowej, żeby geotagować zdjęcia tam, gdzie leżą.
- Wymaga osobnego projektu zabezpieczeń (logowanie, szyfrowanie, ograniczenie do wskazanych folderów) — przedstawić autorowi przed implementacją. Do tego czasu serwer nie przyjmuje połączeń spoza `127.0.0.1`.

### Etap 7 — okno GTK4/libadwaita (później)

- Natywne okno dla GNOME jako drugi interfejs do tego samego silnika: GTK4 + libadwaita (PyGObject), mapa przez libshumate, praca w tle w wątku roboczym z powrotem przez `GLib.idle_add`.
- Funkcje i układ jak w interfejsie w przeglądarce; zgodność z wytycznymi GNOME (HIG).
- Plik `.desktop`, metainfo, manifest Flatpaka (exiftool dołączony do pakietu); ikona jest już gotowa.

## Zasady pracy

- Małe kroki: jedna zmiana, test, commit. Przed większą zmianą krótki opis, co i dlaczego.
- Wyjaśnienia dla autora prostym językiem, po polsku.
- Każda zmiana w `writer.py` wymaga testu potwierdzającego wymagania 1–3.
- Nie dodawać zależności bez uzgodnienia.
- Commity wyłącznie z tożsamością autora z konfiguracji gita, bez żadnych dodatkowych stopek ani dopisków w opisie.
- Komunikaty commitów po angielsku, w trybie rozkazującym.
