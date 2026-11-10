# Menu Zortrax Inventure na Klipperze — CONTROL-R7

Host `RC8-CONTROL-R7-UIFAST` łączy istniejące menu OLED ze wspólnymi
procedurami Klippera. Zachowuje launcher M300 i pięć gałęzi oryginalnego
Inventure: **Models, Maintenance, Material, Settings, Information**.
Oryginalne obiekty menu identyfikują intencję operatora. Jedna warstwa
`ZortraxMenuService` obsługuje tę samą intencję z OLED i G-code/Fluidd.
Właścicielem ruchu, temperatury, kalibracji i zadania pozostaje wykonawca
R7 albo właściwy natywny moduł Inventure.

Wersja UIFAST zachowuje procedury MENU i dodaje
[optymalizację komunikacji i nawigacji przeniesioną z M300](M300_TRANSFER_COMM_OLED_2026-11-10.md).

## Instalacja i pierwszy odczyt

Na MCU `RC8-CONTROL-R7-FACTORY`, `factory_abi=1`, nie trzeba zmieniać
firmware. Aktualizacja dotyczy hosta, menu, sterownika OLED i instalatora.
Jeżeli na MCU jest starsza aplikacja, dostępność czynności R7 będzie
zgłoszona wprost; obowiązuje wtedy wcześniejsza instalacja aplikacji R7.

```bash
git -C "$HOME/inventure-orange-pi" pull --ff-only && python3 "$HOME/inventure-orange-pi/install-orange-pi.py"
```

Instalator wykrywa faktycznie działający Klipper, sprawdza sumy plików,
aktualizuje zależności wspólnie i zachowuje odwracalną kopię. Znany
poprzedni moduł menu z profilem bench jest dopuszczony sumą SHA-256.
Nieznana lokalna modyfikacja powoduje odmowę przed zatrzymaniem usługi.
Nie zmienia `printer.cfg`, UART, bootloadera ani Flash MCU.

Po ponownym połączeniu wykonaj tylko odczyty:

```gcode
INVENTURE_VERSION
INVENTURE_MENU_STATUS
INVENTURE_ZORTRAX OP=STATUS
```

Oczekiwane: host `RC8-CONTROL-R7-UIFAST`, MCU `RC8-CONTROL-R7-FACTORY`,
`procedure_api=1`, `zortrax_menu_api=1`. Zostają dotychczasowe ustawienia
`oem_menu`, `menu_profile` i ekran startowy. Dla `menu_profile: m300`
obowiązują angielskie etykiety. OK na ekranie Klippera otwiera launcher;
wybierz Zortrax Inventure. Gałąź Service / Tests zachowuje osobną diagnostykę.

## Mapa procedur menu

| Pozycja | Wykonanie | Warunek dostępności / zakończenie |
|---|---|---|
| Material → Load from cartridge T0/T1 | Natywne P13, jeśli jest włączone; w profilu serwisowym R7 wspólne AUTO L1–L4 | P13 sprawdza właściwe źródło i tożsamość kasety. R7 jawnie przyjmuje już włożoną kasetę i sprawdzoną drogę; nie udaje autoryzacji RFID. |
| Material → Unload T0/T1 | Natywne P13 albo wspólne R7 UNLOAD U0–U3 | Kontrola wejść i czasu; raport rozróżnia wykonanie procesu i dowód zwolnienia A. |
| Material → Load from spool | Audytowany katalog 17 materiałów modelowych i 5 podporowych, następnie P11 i P13 | Zachowuje wybór drugiego narzędzia; osobna ścieżka szpuli. Nie przekierowuje jej na AUTO kasety. Wymaga włączonych natywnych P11/P13. |
| Maintenance → Hotend maintenance | Wybór T0/T1, przełączanie, grzanie/utrzymanie, purge, czyszczenie i ręczny E | W profilu R7 korzysta z tych samych SELECT/PURGE/CLEAN/MANUAL i HEAT/JOG co konsola. |
| Hotend maintenance → Select nozzle | P04, gdy włączony jest kwalifikowany natywny selektor; w innym przypadku SELECT R7 | Natywny toolframe i wspólny E pozostają pod kontrolą P04. Menu nie przełącza natywnego profilu na SERVICE. |
| Maintenance → Heat the extruder | Jawny wybór T0/T1, nastawy z profilu, potwierdzenie, istniejący PID HEAT | Po gotowości: Finish / heaters off, Feed 20mm, Retract 20mm. Rzeczywista polaryzacja E zależy od T0/T1; kierunek operatora jest logiczny. |
| Maintenance → Platform leveling | Natywne P09, jeden pełny pomiar na kolejne potwierdzenie | Pokazuje następne pole, instrukcję pokrętła, obroty i kierunek. Kandydat głębokości oraz deformacja wymagają osobnego potwierdzenia zapisu. |
| Maintenance → Nozzle alignment | Natywne P10 FULL/Z_ONLY i istniejące przygotowanie/pomiary/wzory | Temperatury są widoczne przed startem. Wybór Y, następnie X; stock_reference dopuszcza tylko neutralne X7. Zapis wspólnego kandydata jest osobnym potwierdzeniem. |
| Maintenance → Move the platform | Skończony krok natywnego Z we wspólnej rezerwacji P02 | Wymaga włączonego natywnego ruchu i prawdziwego homed Z. Zachowuje X/Y/E oraz stan parsera. R7 bottom reference nie jest pełnym Z do druku. Nie włącza surowego ruchu przez przytrzymanie. |
| Maintenance → Fan test | Istniejący test ręczny R7, osobno sześć kanałów, ON/OFF/PASS/FAIL | Zimna drukarka, odczyty tacho i stanu. Wyjście kończy własny test i przywraca politykę chłodzenia; nie anuluje nowego testu z innym numerem run. |
| Models | Lista plików natywnego virtual_sdcard, niezmienna tożsamość podglądu, P15 CHECK, osobny P15 START | Przeglądanie nie uruchamia druku. Start wymaga aktualnego tokenu P15 i nowego potwierdzenia. |
| Print / Job oraz menu pauzy | Bieżące print_stats, virtual_sdcard i P17; zarejestrowane natywne PAUSE/RESUME/CANCEL_PRINT w profilu bez P17 | Działanie odnosi się do tego samego otwartego obiektu pliku i stanu zadania. Wznowienie nie jest automatyczne. Wszystkie bramki właściciela nadal obowiązują. |
| Settings → Sorting / Language | Atomowe ustawienia prezentacji w JSON hosta | Nowy widok unieważnia starą selekcję pliku. Ponowny wybór tej samej wartości nie zapisuje pliku. Launcher M300 pozostaje angielski. |
| Settings → Door lock | Dotychczasowa polityka MCU `INVENTURE_DOOR_POLICY` | Zachowuje jej własne odmowy i reset po restarcie MCU. Brak zapisu fabrycznego NVM przez menu. |
| Information / Service | Rzeczywiste wersje, UART, temperatury, wejścia, wentylatory i dostępne stany właścicieli | Nie przypisuje drukarce wymyślonego numeru seryjnego ani potwierdzenia fizycznego. |

Poziomowanie i alignment nie są nowymi, równoległymi algorytmami w OLED.
Menu przekazuje działania do zweryfikowanych wcześniej P09/P10. Moduły
te nadal sprawdzają referencje XYZ, toolframe, temperatury, geometrię,
interlocki, własność i możliwość zapisu. R7 SERVICE nie nadaje im brakującej
referencji kontaktu PC14, nie ustawia żadnych flag kwalifikacji i nie
przełącza samoczynnie drukarki na kompletny profil natywny.

## Potwierdzenia i wspólny stan

Wejście w czynność ruchu lub grzania otwiera potwierdzenie, domyślnie
No / Back. Dopiero wybór Yes / Start uruchamia procedurę. Widok zawiera
jednorazowy token, parametry, tożsamość właściciela i termin ważności.
Zmiana kursora, nowy widok, zmiana materiału, fazy kalibracji, pliku,
sesji lub restart unieważniają stare uprawnienie.

OLED wymaga potwierdzenia całej ramki przez transport, zgodnej generacji
widoku oraz puszczenia OK przed kolejnym naciśnięciem. Potwierdzenie ramki
nie jest fizycznym odczytem pikseli. Akcję wykonuje dopiero obsługa G-code,
która ponownie sprawdza token i bieżący stan pod mutexem Klippera.

Surowe przyciski panelu mają oddzielne zdarzenie `inventure:menu_buttons`.
Podczas promptu P13 stare zdarzenie `inventure:buttons` nie przekazuje mu
drugiego OK. Menu podaje właściwy, aktualny token do P13 CONTINUE.
Przytrzymany przycisk nie zatwierdza następnego promptu.

Procedura uruchomiona wprost przez INVENTURE_PROCEDURE lub HEAT jest
pokazywana przez tę samą warstwę menu. Ekran pracy pokazuje etap, temperaturę,
nominalne E oraz właściwe operacje Continue/Cancel/Save. Przy pomiarach
P09/P10 stan pozostaje w module kalibracji, a nie w lokalnej kopii OLED.

POWER wraca lub otwiera potwierdzenie anulowania procedury. Długie POWER
zachowuje istniejącą ścieżkę przerwania. W konsoli zawsze pozostaje
`INVENTURE_STOP`. Menu nie wykonuje automatycznego ruchu naprawczego po
anulowaniu. Zakończenie R7 wyłącza grzanie i ruch; chłodzenie ochronne MCU
pozostaje niezależne.

## API G-code / Fluidd

Odczyt oraz dostępność wszystkich zmapowanych akcji:

```gcode
INVENTURE_ZORTRAX OP=STATUS
INVENTURE_ZORTRAX OP=CATALOG
```

Otwarcie potwierdzenia tej samej czynności co na OLED:

```gcode
INVENTURE_ZORTRAX OP=OPEN ACTION=select_t1
INVENTURE_ZORTRAX OP=OPEN ACTION=purge_t0 PURGE_MM=20 TARGET=230
INVENTURE_ZORTRAX OP=OPEN ACTION=heat_t1 TARGET=230
```

To alternatywne, osobne przykłady — OPEN nie uruchamia sprzętu. Odczytaj
`view.token` ze STATUS i dopiero dla tego samego widoku użyj:

```text
INVENTURE_ZORTRAX OP=CONFIRM TOKEN=<aktualny_token>
```

`OP=CHOOSE TOKEN=<aktualny_token> INDEX=<indeks>` wybiera pozycję bieżącego
widoku. Indeksy zaczynają się od zera. `OP=BACK` wraca/otwiera anulowanie;
`OP=CANCEL TOKEN=<aktualny_token>` przerywa wyłącznie własną bieżącą
procedurę. Po zmianie widoku stary token nie działa. Przy długim raporcie
odpowiedź jest podzielona na `V9_ZORTRAX_PART n/N`.

Na panelu nie wpisuje się tokenów — ich wiązaniem zajmują się wspólna
warstwa widoku i potwierdzone ramki OLED. Przy drukowaniu obecne P17
kontroluje również PAUSE/RESUME/CANCEL i rezerwację natywnego zadania.

## Konfiguracja

Temperatury, długości, prędkości i geometria R7 pochodzą z istniejących
`procedure_defaults`, `procedure_t0`, `procedure_t1` i parametrów konkretnej
czynności. [Pełny opis wspólnych modułów](MODULAR_PROCEDURES_2026-11-10.md).

Opcjonalne pola w istniejącej sekcji `[inventure_mcu]`:

```ini
# Domyślnie plik obok printer.cfg; brak zapisu przy samym uruchomieniu.
# zortrax_settings_file: /home/klipper/printer_data/config/inventure_menu_settings.json
zortrax_platform_step: 1.0
zortrax_platform_speed: 1.0
```

Krok i prędkość platformy mają zakres 0,1…5 mm oraz mm/s. Zmiana tych
wartości nie nadaje kwalifikacji natywnemu Z. Ustawienia prezentacji
obejmują obecnie sortowanie i język; nie zapisują bajtów oryginalnego NVM.

## Jawny zakres i pozostałe zależności

- Fizyczna karta SD MCU wymaga osobnego, obecnego czytnika. Ta wersja
  przegląda hostowy virtual_sdcard i wyraźnie podaje źródło. Bez niego
  pokazuje przyczynę braku listy. Lista jest ograniczona do 1000 wpisów.
- P15 rozpoczyna wydruk ze sprawdzonego manifestu. Samo wybranie surowego
  `.gcode`/`.zcode` daje podgląd, a nie obejście bramki P15 ani ukryty konwerter.
- Save print nie tworzy checkpointu przetrwałego po restarcie. P17 nie ma
  takiego kontraktu. Menu informuje o tym i zachowuje bieżący stan pauzy.
- File checking i Ignore material endstops nie ustawiają zastępczych bitów
  prezentacji. Do wykonania potrzebują odpowiedniego publicznego setter API
  właściciela P15/P13. Obecne moduły bez tych API zwracają konkretną odmowę;
  wymagane kontrole transportu i startu nie są wyłączane.
- Menu materiałów podczas pauzy zachowuje rezerwację wydruku. Jeżeli P13
  nie dopuszcza tej nadrzędnej operacji, odmowa pozostaje wiążąca. Menu nie
  zwalnia po cichu druku, aby obejść konflikt.
- Testy programu nie potwierdzają pozycji przełączania, trafienia purge
  do bin, styku PC14 ani fizycznego przebiegu kalibracji. Dostępność
  kwalifikowanych procedur nadal wynika z rzeczywistych modułów i profilu.

## Źródła i weryfikacja

Wykorzystano bieżące odczyty dokumentów z Drive:

- `INVENTURE_MENU_PORT_M300_R16.12_2026-11-03.md`;
- `ZORTRAX_INVENTURE_r0323_OLED_MENU_POWER_RGB_BUZZER_FINAL_2026-10-06.md`;
- audyty wysokości głowic i wyrównania XYZ z 23.10.2026;
- kontrakty P09/P10/P13 z 25.10.2026.

Punktem odniesienia organizacji usług jest
[M300 Classic R16.12 — Procedure inventory](https://github.com/MarcinKowalik-AGH/Zortrax-m300-Klipper/blob/d5a040f22dccfe45380f9550080fd224f15f2ff4/docs/en/PROCEDURE_INVENTORY.md).
Geometria, skale, sensory i protokół pozostają Inventure.

Testy sprawdzają kompletność identyfikatorów oryginalnego menu, katalog
17/5, zerową aktywację po OPEN, tokeny, podwójne/zakolejkowane kliknięcia,
potwierdzoną ramkę, rozdział OK, wykonanie R7, natywne kontrakty P09/P10/P13,
niezmienny plik P15, pauzę właściwego zadania, wyjście z testu wentylatora,
instalację kompletu plików i przywrócenie poprzedniego menu.
