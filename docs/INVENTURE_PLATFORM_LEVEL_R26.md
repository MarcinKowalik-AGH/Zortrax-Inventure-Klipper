# Zortrax Inventure — poziomowanie platformy w pięciu punktach

## Co reguluje ta procedura

W menu fabrycznym funkcja znajduje się pod **Konserwacja → Poziomowanie platformy**. Dysza T0 dotyka pięciu metalizowanych pól tacki. Drukarka prowadzi operatora przez ustawienie trzech śrub, ustala wspólną głębokość odniesienia Z i sprawdza ugięcie środka. Wzajemne offsety T0/T1 pochodzą z oddzielnej kalibracji dysz. Poziomowanie platformy pozostawia te offsety bez zmian i nie tworzy siatki kompensacji wysokości do druku. [Instrukcja producenta, str. 34](https://support.zortrax.com/wp-content/uploads/2022/01/instrukcja_obslugi_inventure_2021.03.30.pdf)

## Przebieg w firmware 1.6.1 r0323

Kolejność poniżej pochodzi z listingu funkcji `0x0801E094`. Instrukcja obsługi z 2021 r. opisuje dokręcenie trzech pokręteł po pierwszym sprawdzeniu środka. W badanej wersji firmware wywołanie komunikatu o pokrętłach występuje już po zaparkowaniu i przed wywołaniem funkcji nagrzewania. [Listing r0323, plik dowodowy 05_motion_homing_leveling.txt](#źródła-projektowe) · [Zortrax Support, Platform Leveling](https://support.zortrax.com/inventure-platform-leveling/)

1. **Przygotowanie.** Firmware opróżnia kolejkę ruchu, sprawdza obecność tacki przez PC13, wyświetla ostrzeżenie o gorącej dyszy i obsługuje rygle. Zeruje dotychczasowy rekord głębokości NVM 52 i unieważnia referencję Z. Wykonuje dolne bazowanie Z w razie potrzeby, ustawia absolutne Z = 40 mm przy F2000, parkuje głowicę, zwalnia rygle i wyświetla prośbę o dokręcenie trzech pokręteł. Operator potwierdza komunikaty środkowym przyciskiem panelu.
2. **Grzanie i wybór dyszy.** Nastawy zależą od dyszy aktywnej przy wejściu do fazy grzania. Po oczekiwaniu na grzałki firmware zawsze wykonuje mechaniczny wybór T0. Mechanizm jedzie od pozycji parkowania przez podejście do selektora przy F8000, dociska selektor przy F250, wraca przez pozycję parkowania i przywraca zapamiętane XY. Korekcję zmiany narzędzia stosuje tylko przy faktycznej zmianie aktywnej dyszy.
3. **Pierwszy pomiar środka.** Pełna referencja Z sprawdza bazowanie Y, następnie X. T0 jedzie nad środek i ustawia fabryczne absolutne Z = 2 mm. Szukanie kontaktu PC14 ma zakres 10 mm, parametr powtórzeń 100. Firmware zapisuje głębokość odniesienia. Następnie ustawia absolutne Z = 3 mm przy F4000. Współrzędna Z w tym opisie jest liczona w układzie fabrycznym. Wartość 3 mm wyznacza położenie stołu podczas przejazdu między punktami.
4. **Narożniki i śruby.** T0 mierzy cztery narożniki w kolejności z tabeli, po czym firmware, jeśli trzeba, prosi operatora o regulację lewego i prawego tyłu, a następnie wspólnej śruby przedniej. Po regulacji ponawia pomiar odpowiedniej pary. Kierunek przejazdu w parze zależy od strony ostatniego pomiaru. Podczas całej sekwencji pomiarowej aktywna jest T0.
5. **Końcowy pomiar środka.** T0 jeszcze raz mierzy pole środkowe. Firmware zapisuje skorygowaną głębokość do rekordu 52, przywraca nastawy spoczynkowe obu dysz, pokazuje wyniki i ocenia ugięcie środka. Na końcu unieważnia referencję Z, wykonuje dolne bazowanie, parkuje i kończy obsługę rygli. [Audyt ruchu, rozdziały 14, 16–20](#źródła-projektowe)

### Nastawy temperatury

| Faza | Nastawa T0 | Nastawa T1 |
|---|---:|---:|
| Przygotowanie do wyboru T0, początkowo aktywna T0 | 200°C | Nastawa spoczynkowa T1 |
| Przygotowanie do wyboru T0, początkowo aktywna T1 | 200°C | 200°C |
| Po mechanicznym wyborze T0, podczas pomiarów | 200°C | Nastawa spoczynkowa T1 |
| Prawidłowe zakończenie pomiarów | Nastawa spoczynkowa T0 | Nastawa spoczynkowa T1 |

Wywołanie `0x0802130C` czeka na gotowość obu torów. Regulator zgłasza gotowość po osiągnięciu nastawy pomniejszonej o 1°C, czyli przy 199°C dla celu 200°C. Nastawy spoczynkowe są konfigurowalne. W tej procedurze brak polecenia ustawiającego temperaturę stołu lub komory. [Listing, adresy 0x0801E11E–0x0801E164 i 0x0801E52C–0x0801E542](#źródła-projektowe) · [Audyt termiki r0323](#źródła-projektowe)

### Pola pomiarowe i ruch Z

Współrzędne w tabeli pochodzą z fabrycznego selektora `0x0801DC20` i odnoszą się do układu OEM. Bezpośrednie polecenie ruchu w układzie liczonym od krańcówek wymaga przeliczenia XY. Pięć różnych pól oznacza co najmniej sześć wizyt, wliczając pierwszą referencję i końcowy odczyt środka.

| Kolejność | ID | Pole | X OEM [mm] | Y OEM [mm] |
|---|---:|---|---:|---:|
| 1 oraz ostatnia, po regulacjach | 2 | Środek | 67,5 | 65,5 |
| 2 | 0 | Przód prawy | 127,5 | 13,0 |
| 3 | 1 | Przód lewy | 7,5 | 13,0 |
| 4 | 4 | Tył lewy | 7,5 | 118,0 |
| 5 | 3 | Tył prawy | 127,5 | 118,0 |

Każdy pomiar po pierwszej referencji wykorzystuje `0x0801DEA8`. Przejazd XY ma zadane F8000 (mm/min). Następnie stół podchodzi małymi krokami do styku PC14 i cofa się do rozwarcia. Parametr 150 oznacza zakończenie po **151 kolejnych cyklach bez uzyskania niższej pozycji rozwarcia**, liczonych od ostatniej poprawy. Zakres szukania wynosi 15 mm. Po pomiarze stół cofa się o faktycznie przebytą drogę do zapamiętanego Z przejazdu. [Audyt ruchu, rozdziały 16 i 19](#źródła-projektowe)

### Jak firmware oblicza korekty

Każdy wynik `H` jest różnicą między zapisanym Z przed próbkowaniem i liczbą kroków podejścia do zmierzonego rozwarcia. Firmware zapamiętuje największe `H` z czterech pierwszych pomiarów narożników jako stałe odniesienie `Href`. Dla kolejnych pomiarów oblicza `Di = Hi − Href`. Jeden milimetr odpowiada 802 krokom Z. Funkcja `trunc` obcina w kierunku zera.

| Element | Wielkość podlegająca ocenie | Akceptacja w krokach Z, oba końce włącznie |
|---|---|---:|
| Lewa i prawa śruba tylna | Osobno D4, D3 | −160 do +80 |
| Wspólna śruba przednia | trunc((D0 + D1) / 2) | −159 do +79 |
| Ugięcie środka | D2 − trunc((D0 + D1 + D3 + D4) / 4) | −401 do +401 (±0,5 mm) |

Stronę za wysoką operator **obniża**, dokręcając wskazane pokrętło przeciwnie do ruchu wskazówek zegara. Stronę za niską **podnosi**, luzując pokrętło zgodnie z ruchem wskazówek. Wielkość obrotu firmware zaokrągla w górę do ćwiartki według wzoru `ceil(float32(float32(abs(delta_steps) × 0,0019953702576463223) × 4)) / 4`. Przykład dla 126 kroków daje pół obrotu. Licznik ogranicza liczbę wyświetlonych poleceń regulacji. Powrót do następnej iteracji wymaga wartości najwyżej 39. Jeden przebieg tylnych śrub może zawierać dwa polecenia. [Komunikaty panelu Inventure](#źródła-projektowe) · [Audyt ruchu, rozdział 19](#źródła-projektowe)

Na końcu `średnia = trunc((H0 + H1 + H2 + H3 + H4) / 5)`, a nowa głębokość w mikrometrach to `trunc(float32(float32(głębokość_odniesienia_µm) − float32(float32(średnia × 1000) / 802)))`. W r0323 zapis poprzedza sprawdzenie ugięcia. Ostrzeżenie o zdeformowanej tacy pozostawia zapisaną wartość. Przerwanie procedury po początkowym wyzerowaniu rekordu 52 nie odtwarza automatycznie poprzedniego wpisu. [Audyt ruchu, rozdziały 19–20](#źródła-projektowe)

### Odniesienie do projektu M300 Classic

W projekcie M300 polecenie `ZORTRAX_LEVEL` obsługuje wspólny przebieg dla panelu i Fluidd, z potwierdzeniami operatora, regulacją śrub i prezentacją wyniku. Jego profil M07 ma cel 260°C i próg gotowości 258°C. Inventure ma własną geometrię pięciu pól i opisaną wyżej sekwencję dla dwóch dysz. [M300 Classic — inwentarz procedur, wpis Autocalibration](https://github.com/MarcinKowalik-AGH/Zortrax-m300-Klipper/blob/d5a040f22dccfe45380f9550080fd224f15f2ff4/docs/en/PROCEDURE_INVENTORY.md)

## Uruchomienie R26.1 na Orange Pi z MCU R13

Host czyta zapisany wynik pełnej kalibracji dysz z `inventure_nozzle_pad.json` obok aktywnego `printer.cfg`. Do wyznaczenia trasy używa zmierzonego środka T0. Względem niego narożniki leżą 60 mm w lewo i prawo oraz 52,5 mm do przodu i tyłu. W pierwszym zapisanym przebiegu R23 środek T0 wyniósł (81,7000; 68,3625) mm. Odpowiada to pierwszemu narożnikowi (141,7000; 15,8625) mm w układzie karetki. Aktualny odczyt z dysku ma pierwszeństwo przed tym przykładem.

W tagu R26 (`620bc96`) instalator odczytywał linię poleceń przed zarejestrowaniem opcji `--configure-leveling`. Kończył się przed zapisem plików i restartem usługi. R26.1 rejestruje wszystkie opcje przed ich odczytem. Na Orange Pi wykonaj polecenia w powłoce przy bezczynnej drukarce. Dotychczasowy katalog R26 może pozostać na dysku.

```bash
git -C "$HOME/inventure-orange-pi" fetch origin tag inventure-platform-level-r26.1 &&
git -C "$HOME/inventure-orange-pi" worktree add --detach "$HOME/inventure-platform-level-r26.1" refs/tags/inventure-platform-level-r26.1 &&
python3 "$HOME/inventure-platform-level-r26.1/install-orange-pi.py" --configure-leveling
```

Opcja instalacyjna dodaje pięć makr poziomowania oraz niezależny przycisk ustawień drzwi do czynnej konfiguracji i restartuje usługę Klipper. W konsoli Fluidd sprawdź `INVENTURE_VERSION` oraz `ZORTRAX_LEVEL_STATUS`. Oczekiwane są host `RC8-CONTROL-R7-PLATFORM-LEVEL-R26.1`, MCU `RC8-CONTROL-R7-PAD-THERMAL-R13` i początkowy stan `idle`. Dotychczasowy MCU R13 obsługuje tę wersję hosta.

1. Osadź oryginalną tackę Inventure z pięcioma czystymi metalowymi polami. Sprawdź, która dysza jest fizycznie opuszczona. Uruchom `ZORTRAX_LEVEL` i wskaż T0 lub T1. Równoważne bezpośrednie wywołanie dla opuszczonej T1 to `ZORTRAX_LEVEL ACTIVE_TOOL=T1`. Na panelu drukarki wybierz **Konserwacja → Poziomowanie platformy**.
2. Zaczekaj na zaparkowanie i komunikat o trzech pokrętłach. Po wykonaniu polecenia wybierz **Gotowe** albo wpisz `ZORTRAX_LEVEL_NEXT`. Przy kolejnych korektach ekran podaje konkretne pokrętło, kierunek i liczbę obrotów. Każda instrukcja ma własne potwierdzenie.
3. Polecenie `ZORTRAX_LEVEL_STATUS` pokazuje bieżący etap i zebrane pomiary. `ZORTRAX_LEVEL_CANCEL` przerywa procedurę. Po powrocie platformy na dolną krańcówkę, zaparkowaniu i zakończeniu sesji wynik przechodzi do `candidate`. Ekran podaje zarówno głębokość platformy, jak i odchylenie środka.
4. Dla zaakceptowanego wyniku wybierz **Zapisz wynik** lub wpisz `ZORTRAX_LEVEL_SAVE`. Klipper zapisuje `inventure_platform_level.json` obok `printer.cfg`. Po przekroczeniu ±0,5 mm ekran wskazuje deformację tacki. Poziomowanie śrubami zmienia ustawienie mechaniczne od razu. Zapisany raport nie nadpisuje zweryfikowanych offsetów dysz. Natywny homing wydruku nadal wyznacza własne odniesienie Z.

### Globalne ustawienie drzwi

W **Ustawienia → Blokada drzwiczek** na panelu albo przez osobny przycisk **ZORTRAX_DOOR_LOCK** w Fluidd można odczytać i zmienić stan przed rozpoczęciem procedury. Otworzenie menu pozostawia obecny wybór. W konsoli `INVENTURE_DOOR_POLICY` odczytuje go z MCU. Pole `enabled` oznacza bieżące ustawienie. Przy `false` obie osłony mogą pozostać otwarte od startu przez regulację pokręteł i wznowienie po zapis. Przy `true` MCU nadzoruje osłony podczas pracy, a kreator wyznacza postoje na regulację. Po restarcie MCU R13 przywraca domyślnie `false`. [Zachowanie OEM i opis globalnego ustawienia](OEM_DOOR_POLICY.md)

### Różnice wykonawcze względem fabrycznej elektroniki

- R26 przejmuje grzanie po wyzerowaniu nastaw natywnych grzałek. W obsługiwanym profilu SERVICE spoczynkowe cele dysz wynoszą 0°C. Jeśli początkowo opuszczona jest T1, host nagrzewa obie dysze, przełącza na T0, kończy grzanie T1 i ponownie bazuje osie po zmianie sesji MCU. Przy T0 na starcie grzeje tylko T0. Globalne ustawienie blokady drzwi pozostaje pod kontrolą użytkownika.
- Gorące przejazdy między polami wykorzystują sprzętowe zatrzymanie MCU przy styku PC14 lub zmianie sygnału tacki PC13. Limit R13 dla ruchu XY z tymi warunkami to 20 000 impulsów/s, około 31,25 mm/s na jednej osi. Przy przejazdach platformy i pomiarach obowiązują również limity osi Z. Przebieg na R13 może więc trwać dłużej niż na oryginalnym sterowniku.
- R26 liczy wysokości i korekty w krokach jak r0323. Zapis do pliku następuje na życzenie operatora po zakończonym przebiegu. Poprzedni raport pozostaje dostępny do tego czasu. R26 nie zapisuje do pamięci fabrycznego firmware.
- Przy włączonej globalnej blokadzie drzwi host parkuje dyszę i przed komunikatem o regulacji potwierdza STOP, wyłączenie napędów i grzania oraz zwolnienie sterowania ryglami. MCU podtrzymuje chłodzenie. Komunikat oznacza, że można otworzyć osłonę i wyregulować pokrętła. Po jej zamknięciu i potwierdzeniu host ponownie bazuje osie. Po korekcie śrub odtwarza tę samą referencję z nowego odczytu PE12 i wraca do właściwej pary pól. Różnicę między odczytami zapisuje w statusie. Przy zmianie większej niż 79 kroków (ok. 0,10 mm) host przerywa porównanie; to próg powtarzalności wznowienia w R26. Otwarcie osłony w trakcie aktywnego ruchu przy włączonej blokadzie przerywa procedurę. R13 odrzuca start pomiaru SERVICE przy wysterowanym ryglu i uniemożliwia jego załączenie w trakcie tej sesji. Dlatego podczas pomiaru MCU nadzoruje czujniki przednich drzwi i pokrywy oraz zatrzymuje urządzenie po otwarciu. Nie ma odczytu mechanicznego położenia rygli. Przy wyłączonej globalnej blokadzie potwierdzenie pokręteł działa bez wymuszania zamknięcia drzwi.

## Walidacja R26.1

Wydanie zawiera 603 testy hosta. Testy wiersza poleceń obejmują `--help`, wywołanie domyślne i wybór opcji `--configure-leveling`. Sprawdzono rzeczywisty instalator na izolowanym modelu usługi Orange Pi, od R25 przez instalację R26.1 po ponowne uruchomienie tej samej komendy. Przy globalnym `OFF` oba pełne przebiegi na symulowanym MCU, początkowo z T0 oraz z T1, kończą się zapisem przy stale otwartych przednich drzwiach i pokrywie. Wynik dla fizycznej drukarki wymaga pierwszej próby.

## Walidacja R26

Przeszło 597 testów hosta i instalatora, osiem programów testowych kodu MCU oraz rozruch rzeczywistej, przypiętej wersji Klippera z konfiguracją świeżą i po migracji. Testy pełnego przebiegu modelują drukarkę po stronie protokołu MCU. Obejmują start z obiema pozycjami selektora, instrukcję tylnej lewej śruby, ponowny pomiar właściwej pary, pomiar środka, powrót na PE12, parkowanie nad koszem i zapis. Testy z włączoną blokadą obejmują otwarcie drzwi przy regulacji, powrót do tej samej referencji, dwie kolejne instrukcje tylnych pokręteł bez ruchu między nimi oraz anulowanie na postoju. Osobna próba odrzuca wynik, gdy po zebraniu pięciu punktów MCU nadal zgłasza aktywną sesję po poleceniu STOP. Pierwszy pełny pomiar pięciu fizycznych pól w R26 pozostaje do potwierdzenia na drukarce.

## Źródła projektowe

- *ZORTRAX INVENTURE V9 / r0323 — MASTER wiedzy do odtworzenia funkcji w Klipperze*, wydanie 1.2 z 7 października 2026, listing `05_motion_homing_leveling.txt`. Wejście kreatora `0x0801E094`, punkty `0x0801DC20`, referencja `0x0801DD34`, pomiar `0x0801DEA8`, wybór odniesienia `0x0801DF60`.
- Audyt ruchu r0323, rozdziały 14 i 16–20. Końcowe porównanie deformacji w listingu pod `0x0801E550–0x0801E582`. SHA-256 analizowanej aplikacji r0323: `9b980ba04531fac4425b77a23969076d24d8f25629fc2a501ac05d262c142268`.
- *ZORTRAX_INVENTURE_CARTRIDGE_PAUSE_END_LEVELING_SLEEP_ERRORS_UI_2026-10-04.md*, rozdział 5, oraz audyt termiki r0323. Dwukanałowe grzanie pod `0x0802130C`, decyzja o wyborze dyszy i jej nastawie pod `0x0801E11E–0x0801E164`.
