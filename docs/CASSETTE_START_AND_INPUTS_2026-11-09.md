# Start ładowania kasety i diagnostyka wejść — 9 listopada 2026

Sprawdzono fabryczną aplikację Inventure 1.6.1 / r0323 bezpośrednio w binarce,
audyt z 2 października, późniejszy audyt z 4 października oraz aktualny adapter
Orange Pi i MCU CONTROL-R5. To analiza oryginalnego programu i portu,
uzupełniona obserwacjami użytkownika; nie protokół uruchomienia pełnego load.

Najważniejszy wynik: **rozpoczęcie procedury ładowania, akceptacja RFID,
automatyczne doładowanie i zliczanie impulsów są osobnymi etapami**.
Wejścia A odpowiadają na ręczne wsunięcie filamentu. Dotychczas nie wykazano,
że mechaniczny przycisk wciskany kasetą jest wejściem B albo osobnym GPIO.

## Źródła i dowody

Fabryczny obraz: 164 784 bajty, baza `0x08010000`, SHA-256:
`9b980ba04531fac4425b77a23969076d24d8f25629fc2a501ac05d262c142268`.

W projekcie obraz znajduje się m.in. w archiwum
`Inventure_r0323_MASTER_GPIO_PERYFERIA_2026-10-02.zip`,
`source/application_r0323.bin`. Wykorzystano też
`ZORTRAX_INVENTURE_KONSOLIDACJA_FUNKCJI_I_PA15_2026-10-02.md` i raport
`ZORTRAX_INVENTURE_CARTRIDGE_PAUSE_END_LEVELING_SLEEP_ERRORS_UI_2026-10-04.md`
z `Audyt firmware.zip`.

[Surowe wartości rekordów kanałów i lokalizacje funkcji](evidence/CASSETTE_R0323_START.json)
odczytano ponownie z binarki. Pozwala to oddzielić liczbę startów podajnika
od liczby zaakceptowanych zboczy na PB12/PD14.

## Co uruchamia procedurę w oryginalnym firmware

Funkcja `0x0801433C` obsługuje Load from cartridge. Kanał 0 wybiera rekord
`0x08029A64` (lewy/model), kanał 1 — `0x08029AA8` (prawy/support).

1. Wywołanie procedury wybiera kanał i zeruje jego bieżący stan/błąd.
   Jeżeli wcześniej wybrano external/spool, ustawienie materiału wraca do
   sentinela kasety `0xFFFE`. Nie jest to automatyczny start od samego włożenia kasety.
2. Ścieżka interaktywna zatrzymuje automatykę kanału, wyświetla instrukcję
   włożenia kasety i czeka na potwierdzenie środkowym przyciskiem panelu.
   Jest to panelowe OK/PD5. Nie jest dowodem przypisania osobnego przycisku
   mechanicznego w gnieździe kasety. Etap włożenia ma też własne sterowanie
   pomocniczym napędem; nie należy utożsamiać go z doładowaniem AUTO.
3. Przy `0x08014420` wywoływany jest callback rekordu `+0x20`:
   T0 `0x0801A964`, T1 `0x0801A96C`. Oba prowadzą do `0x0801A8E4`.
   Ta funkcja korzysta z pozyskania danych RFID (`0x0801A698`) i sprawdza
   klasę toru: lewy/model = 0, prawy/support = 1. Nie odczytuje tutaj
   osobnego pinu mechanicznej obecności kasety.
4. Ujemny wynik zwykłej ścieżki daje komunikat o braku właściwej kasety i cleanup.
   Kod posiada również obejście tej kontroli przez systemowy bit 9
   (`0x08014418`); fizycznej funkcji przycisku kasety nie można z niego wyprowadzać.
5. Po akceptacji program pobiera profil materiału i temperaturę ładowania,
   kontroluje drzwi (`0x08014F74`, z fabryczną opcją pominięcia przez bit 3),
   przygotowuje mechanikę/ruch i czeka na warunki termiczne (`0x0802130C`).
   Potem wybiera narzędzie przez `0x08012098(channel,1)` i włącza automatykę kanału.
6. Dopiero dalsze fazy wykorzystują impulsy transportu, czas bez impulsu,
   ograniczenia ruchu E i liczbę startów pomocniczego podajnika.

## Warunek startu pojedynczego doładowania AUTO

Okresowa ścieżka: `TIM7 → 0x0801A314 → 0x0801A2F0 → 0x0801A21C`.

`0x0801A2F0` pomija kanał w external/spool. W trybie kasety wymaga aktywnego
stanu automatyki (`state == 1`). W niepracującym kanale `0x0801A21C` wymaga:

```text
tryb kasety + aktywna automatyka + brak błędu + B != 0 + A != 0
    → wybór kierunku → uruchomienie podajnika na czas z profilu materiału
```

Getter A w helperze `0x0801A1E4` jest ponawiany po krótkim opóźnieniu,
jeżeli pierwszy odczyt jest zerowy. Getter B wybierany jest przez `0x0801A08C`.
Po ruchu kontroler zatrzymuje kanał i ocenia ponowienia. Dopiero utrzymywanie
się warunku w określonym aktywnym stanie prowadzi po liczniku `>3` do fault 3.

| Sygnał | Lewy T0 | Prawy T1 | Rola potwierdzona w kodzie |
|---|---|---|---|
| A | PD10, `0x08013C20` | PD15, `0x08013C38` | Warunek obecności/gotowości; fizyczna reakcja na filament potwierdzona |
| B | PF1, `0x08013C50` | PG10, `0x08013C68` | Warunek żądania doładowania; przycisk/mechanizm fizyczny nieprzypisany |
| Impulsy | PB12 | PD14 | Oddzielne wejścia nadzoru transportu |
| RUN | PC5 | PG11 | Włączenie pomocniczego napędu |
| DIR | PA8 | PB7 | Kierunek pomocniczego napędu |
| Zegar napędu | PE6 | PE6 | Wspólny TIM9 CH2 |

Gettery A/B zwracają znormalizowany poziom GPIO, bez odwrócenia polaryzacji.
W prześledzonych rekordach i tej ścieżce nie odnaleziono piątego, niezależnego
wejścia cyfrowego „przycisk kasety”. Oznacza to ograniczenie ustalenia
programowego, nie dowód na funkcję lub sposób podłączenia fizycznego guzika.
Może on wpływać na istniejący tor lub mechanikę; potrzeba pomiaru różnicy.

## Impulsy a liczba uruchomień podajnika

| Pole rekordu | T0 | T1 | Znaczenie z prześledzonego zapisu |
|---|---|---|---|
| `+0x2C` | `0x20001330` | `0x20001334` | Liczba zaakceptowanych zboczy PB12/PD14 |
| `+0x30` | `0x2000133C` | `0x20001340` | Czas ostatniego zaakceptowanego zbocza |
| `+0x38` | `0x200018BC` | `0x200018C0` | Liczba uruchomień pomocniczego podajnika |
| `+0x3C` | 12 | 13 | ID pozycji serwisowej, sprawdzone z bajtów obrazu |

`0x0801A0B8` inkrementuje `0x200018BC/0x200018C0` przy starcie podajnika.
Pierwsza faza load (`0x0801455A`) porównuje to pole z bazą +5, więc wymaga
**co najmniej sześciu startów**, a nie sześciu impulsów PB12. Końcowa próba
porównuje to samo pole z bazą +3 (`0x08014758–0x08014784`).

Właściwy licznik zboczy `+0x2C` jest używany w fazie odzyskania transportu,
m.in. `0x080146B0–0x0801473C`: występuje próg co najmniej sześciu zboczy
i limit wieku impulsu około 2000 ticków. Nie należy przenosić nazwy
„impulsy” na wszystkie te liczniki, jak robił to skrót późniejszego audytu.

Wektor EXTI15_10 prowadzi do `0x08011420`; PB12 do `0x08013258`, PD14 do
`0x0801328C`. Przerwanie aktualizuje historię każdego zbocza. Zlicza tylko
zbocze po przerwie większej niż 20 ticków; także odrzucone zbocze aktualizuje
historię. CONTROL-R5 zachowuje tę zasadę w samplerze 20 kHz z oknem ciszy
około 20 ms. Nie znaleziono tu bramkowania odczytów przez RFID czy tryb ARM.

## Co pokazuje obecny Klipper

`SERVICE` ma oczekiwane `armed: 0`. Wejścia i liczniki są nadal odczytywane.
Automatyka P13 wymaga natomiast uzbrojenia NATIVE, aktywnej dzierżawy
transportu, profilu materiału, trybu kasety i pozostałych warunków procedury.
Samo fizyczne wciśnięcie przycisku w SERVICE nie uruchamia P13.

Pełne `INVENTURE_FILAMENT_LOAD` wymaga m.in. włączonego modułu i kwalifikacji,
bezczynnego właściciela operacji, ważnej referencji ruchu/geometrii, zgodnych
temperatur i wentylatorów oraz bieżącego profilu materiału. Dla kasety
dochodzi włączone P12 RFID, zimne urządzenie przy wkładaniu, zerowe cele
temperatur i brak nierozliczonego zapisu RFID. Po potwierdzeniu wkładania
następuje aktywna weryfikacja RFID, wybór narzędzia i grzanie.
To nie jest właściwa procedura do obecnego testu rurki odłączonej od głowicy.

`INVENTURE_FEEDER_TEST` jest osobnym ograniczonym poleceniem SERVICE.
Może poruszyć sam napęd bez RFID, A/B, homingu i grzania. Bez kasety
z zębatkami nie ma podstaw do oczekiwania, że chwyci luźny kawałek filamentu.
W obecnym ustawieniu kaseta jest już zamontowana, a koniec rurki przy głowicy
odłączony — następna próba może dotyczyć wyłącznie suchego transportu.

Zera `sent/received` w statusie UART RFID nie dowodzą sprawności ani awarii
czytnika. Samo `INVENTURE_SENSORS` pyta MCU o liczniki UART; nie wykonuje
skanu karty i nie jest identyfikacją kasety.

## Błąd odczytu hosta i poprawka SENSORS

Poprzednie `INVENTURE_SENSORS` i `INVENTURE_INPUTS` wysyłały zapytania i
natychmiast wypisywały zapisany wcześniej wynik. Normalny polling co 250 ms
maskował ten problem dla długo utrzymanych stanów, ale nie zapewniał odpowiedzi
na aktualnie wydane zapytanie. Nie wolno uznawać tego za udowodnioną przyczynę
wszystkich dotychczasowych zer przy przycisku.

`RC8-CONTROL-R5-SENSORS` czeka na odpowiedzi istniejących komend MCU.
Raport zawiera `snapshot_fresh`, `refresh_ms` i `inputs_age_s`. Zapytania
korzystające z pinned `SerialRetryCommand` przywracają stałe subskrypcje
wejść, stanu płyty i UART także po błędzie. Poprawka dotyczy wyłącznie hosta;
CONTROL-R5, bootloader 1.7 i UART 750000 pozostają kompatybilne.

## Odczyt innych wejść bez kolejnego flashowania

Nowe `INVENTURE_CASSETTE_DIAG` korzysta z istniejącej w CONTROL-R5 komendy
`debug_read`. Adresy są stałe: wyłącznie MODER i IDR portów GPIOA–GPIOG.
Nie ma parametru dowolnego adresu, zapisu rejestrów, zmiany trybu pinu ani
podciągania. Polecenie wymaga bezczynnego SERVICE. MODER jest odczytywany
po to, aby odróżnić wejścia od wyjść, ADC i funkcji UART/SPI.

Przy zamontowanej kasecie i wciśniętym przez nią przycisku:

```gcode
INVENTURE_CONTROL MODE=SERVICE
INVENTURE_CASSETTE_DIAG ACTION=BASELINE
```

Po wyjęciu kasety / zwolnieniu przycisku:

```gcode
INVENTURE_CASSETTE_DIAG ACTION=COMPARE
```

Raport podaje `changes` z rzeczywistymi nazwami pinów, także spoza 16-elementowej
listy, surowe poziomy PB12/PD14, porty, tryby i przyrosty liczników względem bazy.
Skan portów jest sekwencyjny, nie jednoczesny. `changes` obejmuje piny będące
wejściami w obu próbkach; zmiany trybu są podane osobno. Nieużywane wejścia
mogą być pływające. Reakcja nieznanego pinu wymaga powtórnego powiązania
z przyciskiem, zanim przypiszemy mu funkcję sprzętową.

Po ponownym zamontowaniu kasety, przy rurce odłączonej od głowicy, można
sprawdzić lewy napęd krótkim poleceniem w kierunku przeciwnym do zgłoszonego
wcześniej cofania DIR0:

```gcode
INVENTURE_CASSETTE_DIAG ACTION=BASELINE
INVENTURE_FEEDER_TEST CHANNEL=0 DIRECTION=1 DURATION_MS=1500
G4 P1800
INVENTURE_CASSETTE_DIAG ACTION=COMPARE
```

To test transportu, bez grzania i ruchu głowicy. Potrzebna jest obserwacja,
czy materiał faktycznie wychodzi z wolnego końca rurki. Brak impulsów
nie zostaje automatycznie zamieniony w diagnozę uszkodzenia czujnika.

## Stan potwierdzeń fizycznych

- Lewy T0_A/PD10: pusty 0 → wsunięty 1 → wyjęty 0, PASS.
- Prawy T1_A/PD15: pusty 0 → wsunięty 1 → wyjęty 0, PASS.
- PF1: w historii widziano 1 i 0, bez jednoznacznego przypisania mechanizmu.
- PG10: fizyczna reakcja nadal niepotwierdzona.
- PB12/PD14: w ręcznych próbach rurki liczniki stale 2/1, przyrost 0.
- O 13:34:35 użytkownik potwierdził kasetę z filamentem i wciśnięty przez
  nią przycisk. Raport nadal miał cztery A/B = 0, liczniki 2/1 i RFID 0/0.
  Funkcja tego przycisku oraz rzeczywisty transport z kasety pozostają do
  pomiaru nowym odczytem. Nie uruchomiono pełnego load/grzania.

Walidacja hosta: 70 testów automatycznych, w tym odczyt nowych wartości
zamiast starego cache, odtwarzanie subskrypcji przez rzeczywiste klasy pinned
Klippera, dekodowanie protokołu istniejącego MCU, stała lista rejestrów
tylko do odczytu, wykrywanie nieznanego GPIO i aktualizacja aktywnego hosta.
To testy oprogramowania; fizycznego przycisku nie kwalifikują.
