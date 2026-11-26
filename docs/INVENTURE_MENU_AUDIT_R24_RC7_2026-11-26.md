# Inventure — audyt pełnego menu OLED i Fluidd, R24 RC7

Stan źródeł: 26.11.2026. Kandydat instalacji: `inventure-home-r24-rc7`.
Marker interfejsu: `menu_audit_release: RC8-OPERATOR-MENU-R24.5`.

Menu odtwarza wszystkie **66 rekordów menu oryginalnego firmware r0323**.
Nie oznacza to zakończenia implementacji wszystkich funkcji drukarki.
Poniżej rozdzielono obecność pozycji, dostępność wykonania i potwierdzenie
na sprzęcie. Odtworzone etykiety pozostają po angielsku, zgodnie z projektem.

Źródła porównania:

- [66 rekordów OEM wraz z adresami i relacjami](validation/INVENTURE_OEM_MENU_R0323.json).
- [Powiązania oryginalnych rekordów z aktywnymi stronami](../restored/operator_menu_r23/inventure_oem_menu.py).
- [Aktywna warstwa prezentacji RC7](../restored/menu_audit_r24/inventure_menu_presentation.py).
- M300: aktywny `M300_HOST/payload/m300_menu_ui.py` i renderer grafiki na gałęzi `orange-pi`.
- Aktywne makra są parsowane przez rzeczywisty, przygotowany Klipper; granice
  sprzętowe MCU, czujników, silników i temperatur są modelowane w testach.

Legenda: **OEM** — pozycja oryginalna; **+** — nasz dodatek. **Aktywne** oznacza
podłączony istniejący wykonawca, a nie fizyczną akceptację całej procedury.
**Częściowe** oznacza działający fragment; **Brak** oznacza brak wykonawcy
w ostatnim dostarczonym profilu. Nie uruchamiano drukarki podczas tego audytu.

## Pełne drzewo Zortrax Inventure

Na ekranie głównym jest nasz wybór **Klipper / Zortrax Inventure**.
W Zortrax Inventure zachowano pięć głównych działów OEM. Każda strona ma
`Back`; nie powtarzamy tego w każdym wierszu. Okna `No / Yes`, ostrzeżenia
i powielone ścieżki materiałowe z menu pauzy także są odwzorowane.

| Menu główne → podmenu → pozycja | Pochodzenie | Stan wykonania i ograniczenia |
| --- | --- | --- |
| Models → Browse SD card → lista plików / podgląd / wybór | OEM; przeglądarka hosta | Częściowe: działa lista plików hosta. Start pełnego cyklu wymaga nieaktywnego P15; fizyczny czytnik MCU nie jest skonfigurowany. |
| Maintenance → Platform leveling | OEM | Aktywna pięciopunktowa procedura wstępnego poziomowania, wskazówki śrub i zapis wyniku. Wcześniej potwierdzona przez operatora. **Nie jest to mesh do kompensacji podczas druku.** |
| Maintenance → Nozzle alignment calibration | OEM | Podłączona istniejąca kalibracja XY T0/T1 i jej potwierdzenie. RC7 dodaje brakujące makro grupy. Pełny wydruk wzorca i zapis offsetów wymagają odbioru sprzętowego. |
| Maintenance → Hotend maintenance → Left hotend maintenance (model) | OEM | Aktywne podmenu T0; szczegóły poniżej. |
| Maintenance → Hotend maintenance → Right hotend maintenance (support) | OEM | Aktywne podmenu T1; szczegóły poniżej. |
| Maintenance → Home X | + | Aktywne zaakceptowane bazowanie, pomiar rozwarcia i dynamiczne zero, odjazd 5 mm, trzymanie silników. |
| Maintenance → Home Y | + | Jak X, dla Y. |
| Maintenance → Home XY | + | Aktywne wspólne wywołanie istniejących bazowań. Osobnej pozycji Home XY nie było w OEM. |
| Maintenance → Home Z | + | Aktywne zaakceptowane Z: używa znanych i nadal trzymanych X/Y; wykonuje tylko brakujące osie. Kontrola tacki pozostaje aktywna. |
| Maintenance → Home XYZ | + | Aktywne wspólne wywołanie; ponownie wykorzystuje ważne XY przed Z. |
| Maintenance → Heat the extruder → Model nozzle T0 / Support nozzle T1 | OEM; wybór narzędzia w naszym dialogu | Aktywne grzanie/utrzymanie temperatury przez istniejącego właściciela SERVICE. |
| Maintenance → Move the platform → ruch w górę / w dół | OEM | Aktywne kroki natywnego ruchu przy poprawnym bazowaniu i warunkach wejścia. |
| Maintenance → Fan test → kanały F0–F5 → sterowanie / koniec testu | OEM; rozszerzona diagnostyka | Aktywne sterowanie istniejącymi kanałami i odczyt tachometrów. Nazwy i przypisania fizyczne bez zmian. |
| Maintenance → Current procedure | + | Aktywny wspólny podgląd właściciela, postępu, odpowiedzi operatora i anulowania. |
| Material → Model → Unload the model material | OEM | Istniejąca procedura T0 podłączona. Pełna sekwencja z rzeczywistym filamentem wymaga osobnej akceptacji. |
| Material → Model → Load the model material → Load from cartridge | OEM | Podłączony T0; obsługa interaktywna i zapisane parametry materiałowe. Pełny automat kasety z P13/RFID nie jest uruchomiony w profilu. |
| Material → Model → Load the model material → Load from spool → Select material | OEM | Katalog 17 materiałów modelowych i istniejące kroki obsługi. Pełna automatyka nie jest zakończona. |
| Material → Support → Unload the support material | OEM | Istniejąca procedura T1 podłączona; odbiór pełnej sekwencji z filamentem pozostaje. |
| Material → Support → Load the support material → Load from cartridge | OEM | Podłączony T1; ograniczenia automatu kasety jak dla T0. |
| Material → Support → Load the support material → Load from spool → Select material | OEM | Katalog 5 materiałów podporowych; ograniczenia jak wyżej. |
| Material → Filament recovery → zgłoszenie / źródło / brak / pęknięcie / anulowanie | + | Istniejąca diagnostyka i prowadzona obsługa zacięcia. Pełna integracja automatu kaset wymaga dalszych testów. |
| Settings → Language → English | OEM | Aktywne. |
| Settings → Language → Polski | OEM | Częściowe: pozycja jest, wyświetla informację o angielskim interfejsie. Pełne tłumaczenie nie jest zaimplementowane. |
| Settings → Models sorting → Date/time ↓ / Date/time ↑ / File name / File size | OEM | Aktywne sortowanie przeglądarki hosta. |
| Settings → Door lock → Door lock enable / Door lock disable | OEM | Podłączona wspólna polityka drzwi; warunki MCU pozostają nadrzędne. |
| Settings → File checking → Enable / Disable .zcode file checking | OEM | Brak wykonawcy w ostatnim profilu; interfejs zgłasza niedostępność. |
| Settings → Ignore material endstops | OEM | Brak wykonawcy w ostatnim profilu; brak obejścia czujników. |
| Settings → Light → White / Off / Sleep lighting | + | Aktywne istniejące sterowanie oświetleniem. |
| Settings → Sleep mode | + | Aktywna obsługa wygaszania ekranu. |
| Settings → OLED brightness / OLED dim time / OLED sleep time / OLED timers | + | Aktywne wspólne ustawienia wyświetlacza. |
| Settings → Sleep lighting / OLED icons | + | Aktywne ustawienia oświetlenia przy uśpieniu i ikon. |
| Information → Firmware version | OEM | Aktywne dane hosta/MCU. |
| Information → Printer information | OEM | Częściowe: aktualne dane hosta/MCU/UART; pełny zestaw fabrycznych liczników i identyfikatorów OEM wymaga uzupełnienia. |
| Information → Help | OEM | Aktywne informacje dostępnych poleceń. |

Oba podmenu hotendu mają te same nasze rozszerzenia, z właściwym T0/T1:

| Pozycja hotendu | Stan |
| --- | --- |
| + Select nozzle | Istniejący wybór narzędzia; wspólne parametry i właściciel OLED/Fluidd. |
| + Heat / hold | Istniejące grzanie/utrzymanie. |
| + Purge and clean | Istniejący purge i czyszczenie. |
| + Clean nozzle | Istniejące czyszczenie. |
| + Feed 20 mm / Retract 20 mm | Istniejący ręczny transport w dopuszczonym oknie temperatury. |

## Oryginalne menu pauzy i przerwania

| Pozycja / podmenu | Pochodzenie | Stan |
| --- | --- | --- |
| WARNING: Hot extruder | OEM | Odtworzone ostrzeżenie. |
| Resume the print job | OEM | Podłączone istniejące wznowienie; wymaga rzeczywiście wstrzymanego zadania. |
| Stop the print job → Cancel the print job? → No / Yes | OEM | Podłączone istniejące zatrzymanie i wspólne potwierdzenie. |
| Material/Maintenance → Material | OEM | Prowadzi do obu pełnych gałęzi Model/Support opisanych wyżej, w tym unload, cartridge i spool. |
| Material/Maintenance → Save the print | OEM | Brak trwałego checkpointu umożliwiającego odtworzenie wydruku po restarcie. |
| Material/Maintenance → Hotend maintenance → Left / Right | OEM | Te same hotendy i warunki właściciela; brak pełnej integracji wymiany materiału z natywnym wydrukiem. Konflikt właścicieli nie jest obchodzony. |
| No / Yes oraz ogólne okna pytania/anulowania | OEM | Odtworzone; stare tokeny po anulowaniu są odrzucane. |

## Wspólne sterowanie: OLED, Fluidd, makra

Istnieje jeden obiekt menu `ZortraxMenuService`, jedna bieżąca procedura,
te same parametry, odpowiedzi i tokeny. Fizyczny panel oraz dialog Fluidd
wybierają istniejące publiczne wejścia. Niskopoziomowe polecenia diagnostyczne
MCU nadal istnieją; nie każda komenda diagnostyczna ma osobny dialog menu.

| Pozycja / cel | Makro lub publiczne wejście |
| --- | --- |
| Główne Zortrax Inventure / Klipper | `INVENTURE_MAIN_MENU` / `INVENTURE_KLIPPER_MENU` |
| Maintenance / Material / Settings / Information | `INVENTURE_MAINTENANCE` / `INVENTURE_MATERIAL_MENU` / `INVENTURE_SETTINGS` / `INVENTURE_INFORMATION` |
| Home X / Y / XY / Z / XYZ | `INVENTURE_HOME_X` / `INVENTURE_HOME_Y` / `INVENTURE_HOME_XY` / `INVENTURE_HOME_Z` / `INVENTURE_HOME_ALL` |
| Wstępne poziomowanie | `INVENTURE_LEVEL` → wspólny `INVENTURE_ZORTRAX` → istniejący `INVENTURE_PLATFORM_LEVEL` |
| Kalibracja XY dysz | **Nowe** `INVENTURE_NOZZLE_ALIGNMENT` → wspólny dialog → istniejący wykonawca alignment |
| Wybór / grzanie / czyszczenie / purge | `INVENTURE_NOZZLE_SELECT` / `INVENTURE_HEAT` / `INVENTURE_CLEAN` / `INVENTURE_NOZZLE_PURGE`, parametr `TOOL=T0/T1` |
| Ładowanie / wyładowanie | `INVENTURE_LOAD` / `INVENTURE_UNLOAD`, istniejący `INVENTURE_MATERIAL` |
| Wentylatory | `INVENTURE_FANS`, `INVENTURE_FAN_TEST_START`, `INVENTURE_FAN_CHANNEL`, `INVENTURE_FAN_TEST_END` |
| Ruch platformy | `INVENTURE_PLATFORM_UP` / `INVENTURE_PLATFORM_DOWN` |
| Światło / jasność / timery / uśpienie | `INVENTURE_LIGHT` / `INVENTURE_OLED_BRIGHTNESS` / `INVENTURE_OLED_TIMERS` / `INVENTURE_SLEEP_MODE` |
| Odpowiedź / powrót / zatrzymanie | `INVENTURE_MENU_CHOOSE`, `INVENTURE_MENU_BACK`, `INVENTURE_STOP`; OLED używa tego samego stanu i tokenów |

Makra operatora należą do istniejącej grupy **ZORTRAX INVENTURE** w Fluidd.
Nowe makro kalibracji jest pobierane przez dotychczasowy mechanizm grupowania.
Nie zmieniono zapisanych grup użytkownika ani procedur wykonawczych.

## Znalezione błędy i poprawki RC7

| Błąd | Poprawka |
| --- | --- |
| 16 pozycji gałęzi Klipper bez ikony; brak ikon w części dialogów T0/T1, grzania, wentylatorów, światła i ustawień | Uzupełnione dziedziczenie i jawne przypisania. Sprawdzone 133 pozycje stron oraz 161 pozycji dialogów. |
| Generyczne / niewłaściwe obrazy load/unload i pauzy | Dodano komplet 17 deskryptorów OEM z ROM. Odtworzono osobne T0/T1 oraz odbicie poziome wskazane przez oryginalny renderer. |
| Własne czteroklatkowe ikony miały wyłączoną animację | Włączono istniejące animacje dla odpowiednich ikon. |
| Przewijanie wspólnej strony mogło mieć inne okno na OLED i w podglądzie | Wspólna nawigacja korzysta z rzeczywistego okna i wyboru tej samej strony. |
| Brak przycisku grupy makr dla istniejącej kalibracji XY | Dodano `INVENTURE_NOZZLE_ALIGNMENT`, prowadzące do tego samego dialogu co OLED. |
| Obrazy trwającej procedury menu mogły zamarzać przy blokadzie aktualizacji podczas grzania | Rozszerzono istniejący, ograniczony transfer pikseli na aktywne procedury menu. Flaga blokady, harmonogram odpytywania, budżet UART, ACK i właściciele urządzeń pozostają bez zmian. |
| Ochrona instalatora odrzucałaby dodanie jednego nowego makra | Dozwolone jest wyłącznie dodanie dokładnej kanonicznej definicji tego makra, gdy wcześniej nie istniało. Każda zmiana istniejącej konfiguracji nadal powoduje odmowę. |

**Grafika:** każda sprawdzona pozycja ma rozwiązywalną ikonę; obrazy OEM
pozostają oryginalnymi obrazami statycznymi. Nasze symbole czynności i Home
są animowane tam, gdzie mają cztery klatki. Nie twierdzimy, że OEM miał
animację w każdej pozycji ani że fizycznie obejrzano wszystkie ekrany RC7.
Galeria `INVENTURE_MENU_AUDIT.png` zawiera rzeczywisty bufor renderera,
nie makietę. Poprzednia galeria `R24_HOME_OLED.png` pokazuje wszystkie pięć
naszych ikon bazowania w czterech klatkach.

## Potwierdzone, do sprawdzenia, brakujące

| Kategoria | Wynik |
| --- | --- |
| Potwierdzone wcześniej na drukarce | X/Y/Z zaakceptowane przez operatora; wstępne pięciopunktowe poziomowanie i jego zapis; wcześniej odnotowane etapy T0/T1 i czyszczenia. Szczegóły akceptacji Home są w przypiętym zapisie RC4. |
| Zweryfikowane programowo w RC7 | Całość drzewa OEM, wspólny wybór/obraz/przewijanie; 800 zakończonych żądań Home i 184 odmowy; 24 uruchomienia sześciu procedur dla T0/T1 z obu interfejsów po Home, porównanie parametrów, anulowanie i odrzucenie starych tokenów; zgodny dialog kalibracji. |
| Wymagają odbioru na drukarce | Nowa prezentacja RC7, leniwe ponowne użycie XY z RC5, wszystkie wejścia i anulowanie T0/T1, końce pełnych sekwencji z filamentem, kalibracja wzorca XY i zapis, obsługa w trakcie rzeczywistego wydruku/pauzy. |
| Wymagają implementacji / konfiguracji | Polski interfejs, pełny P13 kaset/RFID i feederów, P15 start/resume całego cyklu, trwały Save print, kontrolowane ustawienia File checking i Ignore material endstops, fizyczne SD MCU, pełne dane/liczniki OEM. |
| Osobny brak poza menu OEM | Prawdziwy mesh w Tune do kompensacji ruchu stołu podczas druku. W ostatnim profilu nie ma aktywnego `bed_mesh` ani kompletnej ścieżki jego pomiaru. Wstępne poziomowanie nie zastępuje tego zadania. |

## Zachowanie zaakceptowanego bazowania i instalacja

Między RC6 i RC7 **identyczne bajtowo** pozostają:
`inventure_operator_home.py`, `inventure_native_z.py`, `inventure_mcu.py`
i `inventure_z_home.py`. Nie zmieniono ruchów, temperatur, pomiaru rozwarcia,
5 mm, trzymania silników, czyszczenia, tacki ani przekazywania właściciela.
Zmieniono sześć plików warstwy menu/grafiki, wliczając dwa nowe zasoby.

| Plik wykonawczy | SHA-256 RC6 = RC7 |
| --- | --- |
| inventure_operator_home.py | `79dc8b712cf54f0e86b67936d1569690071345e8662a29d4ae026236acc53352` |
| inventure_native_z.py | `4e6643642cc88868a0b46cc37abbf9c3d9ae30850aed0352e51ddd03d2fa380a` |
| inventure_mcu.py | `2f9216e819c5d9ca34330db87bbe4032eacbde2dc73aa05ec51316532a0dfe6b` |
| inventure_z_home.py | `030ab6cf028c8321fa877d0c7d553162061722d66f2a7af14b04f452460621cd` |

MCU R24 pozostaje identyczne. **Nie trzeba ponownie wgrywać MCU**, jeśli
działa już `RC8-CONTROL-R7-HOME-R24-OLED`. Szczegóły aktualizacji, restartu
bez aktywnego zadania i automatycznego rollbacku: [instrukcja R24](INVENTURE_HOME_R24.md).
Odczyt `INVENTURE_ZORTRAX OP=STATUS` po aktualizacji ma dodatkowe pole
`menu_audit_release: RC8-OPERATOR-MENU-R24.5`. Sam GitHub nie potwierdza
instalacji na Orange Pi; tę potwierdza dopiero odczyt uruchomionego hosta.

Dowody w wydaniu: `INVENTURE_MENU_AUDIT.json`, `INVENTURE_MENU_AUDIT.png`,
`R24_HOME_CYCLES.json`, `R24_HOME_OLED.png` oraz wynik CI testów hosta,
regresji MCU i kompilacji ARM. Wszystkie nowe próby są programowe;
`hardware_tested` pozostaje `false`.
