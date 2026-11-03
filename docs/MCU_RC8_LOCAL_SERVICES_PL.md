# Inventure MCU RC8.0 — chłodzenie lokalne, odzyskiwanie OLED i wydajność

Data: 3 listopada 2026. Host RC7.5 bazuje na commicie
`013e9fcb0578147227f8f5301502b375f73506ef`.
Nowa aplikacja MCU: **`P01-P17-RC8.0-LOCAL`**, STM32F207, start `0x08010000`.
InventureBoot, P4-PERSIST, UART i konfiguracja mechaniki pozostają takie same.

## Co było w poprzednim MCU

Przy błędzie/nieuzbrojonej drukarce MCU wymuszał chłodzenie niezależnie od
temperatury. Nie było termostatu umożliwiającego jego wyłączenie po ostygnięciu.
Lokalna strona „Waiting for Klipper” działała tylko przed pierwszym przejęciem
OLED. Późniejszy stop/timeout/shutdown wygaszał panel na stałe dla tej sesji.

Porównano rzeczywiste źródła M300 R14.4 (`m300_cooling.inc`, `m300_board.c`,
`m300_oled_local.inc`) z pakietu `M300_R14_4_GUARD_SNAPSHOT_THERMAL_2026-11-01.zip`
oraz host R15.6. Przeniesiono zachowania; implementacja, osiem sensorów,
GPIO i format IVB pozostają właściwe dla Inventure.

## Chłodzenie MCU

| Stan | Zachowanie RC8.0 |
|---|---|
| Zimny start bez Orange Pi | Wyjścia najpierw w bezpiecznym stanie; lokalny pomiar wszystkich ośmiu temperatur, potem termostat |
| Nieuzbrojona drukarka, utrata hosta lub zwykły shutdown | ON, gdy dowolny z ośmiu czujników osiąga 50°C; OFF dopiero, gdy wszystkie są na poziomie najwyżej 45°C |
| Zakres 45–50°C | Zachowanie poprzedniego stanu, bez oscylacji |
| Brak próbki, próbka starsza niż 1 s, poza zakresem lub timeout ADC | Pełne chłodzenie ochronne; lokalny pomiar nie zezwala na ARM |
| Zatrzaśnięty błąd kabla, czujników/termiki lub tachometru V9 | Pełne chłodzenie pozostaje wymuszone |
| Uzbrojona drukarka ze zdrową sesją | Dotychczasowe natywne PWM i polityka wentylatorów Inventure, w tym własna krzywa hotendu |

W lokalnym trybie pięć kanałów chłodzenia jest obsługiwanych wspólnie na
podstawie maksimum ośmiu temperatur. Kanał nadmuchu modelu pozostaje OFF.
To jawna polityka lokalnego chłodzenia, nie rekonstrukcja polityki OEM.
Dla T0/T1 granice są porównywane przed zaokrągleniem przeliczenia `660×raw/4095`:
ON od raw 311, OFF do raw 279. Raw 280 nie powoduje przedwczesnego wyłączenia.

Przed pierwszym natywnym `gpio_adc_setup` lokalny czytnik korzysta z tego
samego sterownika ADC i przypiętych pinów PC0/PF8/PC1/PC2/PA4/PF3/PF4/PC4.
Pierwsza konfiguracja natywna kończy lokalne konwersje, zanim zostaną
uruchomione timery ADC hosta. Lokalne próbki mają oddzielne flagi/czasy i
nie są zapisywane jako pomiar kwalifikujący ARM. Nie zmieniamy wspólnych
preskalerów ADC ani nie uruchamiamy konkurencyjnego ADC2.

Natywny `analog_in_shutdown` przypiętej bazy Klippera podtrzymuje timery
pomiarowe. MCU korzysta z tych świeżych odczytów także po shutdown. Jeżeli
skonfigurowano tylko część sensorów lub pomiar przestanie działać, chłodzenie
pozostaje w stanie ochronnym. Usługa działa po usunięciu timerów ruchu;
nie przywraca uzbrojenia, grzałek, feederów ani wcześniejszego zadania.

## Ekran po utracie hosta

| Zdarzenie | Lokalny widok |
|---|---|
| Start bez hosta | Waiting for Klipper, wersja aplikacji/boot i rzeczywisty baud P4 |
| Wygasła dzierżawa OLED (2 s) lub zwolnienie przez hosta | Waiting for Klipper / Connection lost or released |
| Globalny shutdown | MCU stopped / Cooling: automatic |
| Ponowne przejęcie OLED bez globalnego shutdown | Wymagana nowa generacja hosta |
| Host wysyła starą operację po utracie sesji | Odrzucenie; brak nadpisania lokalnego statusu |
| Globalny zatrzask błędu | BEGIN i sterowanie napędami nadal zablokowane; ekran diagnostyczny może działać |
| Brak odpowiedzi SPI | Ograniczony czas próby i błąd lokalnego OLED; bez nowego globalnego shutdown |

Żądanie przejęcia ekranu nie wykonuje SPI w IRQ. Rysowanie odbywa się w
krótkich zadaniach, do 32 B na porcję, z przerwaniami włączonymi podczas
transmisji. Zmiana właściciela/shutdown w trakcie porcji unieważnia jej epokę.

## Optymalizacje i poprawki

- Bufor lokalnego obrazu: 8192 → **128 B**, oszczędność 8064 B samego bufora.
  Całkowity wynik RAM po dodaniu stanu chłodzenia należy odczytać z mapy/ELF
  nowego builda; nie utożsamiamy tych dwóch liczb.
- O(1) wybór glifu z tablicy offsetów, zamiast przechodzenia do 95 rekordów
  dla każdego znaku. Piksele są porównywane z niezależnym pełnym rendererem.
- W głównej pętli renderowany jest jeden wiersz, a SPI ma jeden timeout dla
  całej porcji. Nie ma pętli opóźniającej uruchomienie całego wyświetlacza.
- Częsty tor STEP pomija klasyfikację wentylatorów/grzałek. Zachowano
  kontrolę uzbrojenia i jednokrotne rozliczanie fizycznego E w P13.
- Zliczanie tachometru/próbkowania iteruje po faktycznych zboczach. Progi
  quiet/fan/guard oraz **20 000 próbek/s** pozostają bez zmian.
- Sampler startuje w zadaniu po zakończeniu inicjalizacji zegara. Nowy miękki
  timer usług planuje termin od aktualnego czasu; nie modyfikuje timingu STEP
  ani samplera marginesem 200 µs z M300.
- Poprawiono pierwszy okres obserwacji wymaganego tachometru po długim idle:
  rozpoczyna pełne 10 s, zamiast porównywać historyczny znacznik.
- Statyczna polityka chłodzenia nie powtarza zmian trybu GPIO. Jej ocena ma
  okres 20 ms; w IRQ znajduje się tylko wybudzanie usług. Podczas lokalnego
  odczytu/rysowania usługi mogą budzić się co 2 ms, w idle co 20 ms.

Nie zmieniono mikrokroków, szerokości STEP, geometrii, homingu, PID,
limitów prędkości/przyspieszeń ani zachowania suwaka 150% Fluidd.

## Walidacja i zakres dowodu

Lokalnie przechodzą: **591 testów regresji + 1 prywatny reference SKIPPED**,
**28 testów akceptacji** oraz **38 testów integracji/instalatora**, w tym
uruchomienie rzeczywistego C płyty i panelu na modelach rejestrów/API.
Ponadto zachowano 10 wcześniejszych zestawów C P4/UART/OLED.

Nowe próby obejmują wszystkie 4096 wartości RAW obu hotendów w obu stanach
histerezy, ośmiokanałowy zimny/gorący start, wygaśnięcie próbek i wrap,
timeout ADC, odrzucenie lokalnych próbek dla ARM, trwałe przekazanie ADC,
shutdown i podtrzymanie chłodzenia, brak zbędnych zapisów GPIO, przywrócenie
AF sprzętowych fanów, pełny czas tach po idle oraz kombinacje zboczy fanów.
Próby panelu porównują pełne 8192 B obrazu startup/timeout/shutdown z
niezależnym rendererem i sprawdzają zmianę właściciela w trakcie bajtu.

GitHub Actions wykonuje przypięty GNU ARM 14.2.Rel1, pełny link F207,
kontrolę wektorów, mapy pamięci, słownika, nowych stałych i odpowiedzi oraz
weryfikację IVB. Wymagany jest sukces całego konkretnego przebiegu.
Nie wykonano fizycznego pomiaru PWM/przepływu wentylatorów, temperatur,
czasu reakcji OLED ani wydajności STEP przy równoczesnym obciążeniu.

Rejestry ADC sprawdzono z [RM0033 STMicroelectronics](https://www.st.com/resource/en/reference_manual/cd00225773.pdf);
implementacja zachowuje natywny sterownik i jego własność. Wytyczne
transferu i rozdzielenia testów programowych od sprzętowych pozostają w
[dokumencie Drive](https://drive.google.com/file/d/1lA0C82j_Qn3W3LZ6LxM7boSnTxHTOy6X/view).

## Instalacja nowego MCU i pasującego hosta

Pobierz artefakt **inventure-rc8_0-mcu-rc7_5-host-zero3-…** wyłącznie
z zakończonego SUCCESS przebiegu. Rozpakuj go. Na Orange Pi, w katalogu `HOST`:

```sh
python3 scripts/install_host.py ~/klipper
```

Po poprawnym dry run, przy niepracującej drukarce:

```sh
sudo systemctl stop klipper
python3 scripts/install_host.py ~/klipper --apply --confirm-service-stopped
```

Zapisz ścieżkę backupu. Ten krok aktualizuje pasujące 14 dodatków, dane fontów
i moduł serial. W istniejącej sekcji `[inventure_oled]` zastosuj opcje z
`OLED_FAST_RC7_5.cfg`, w tym `oem_start_screen: klipper` i `menu_profile: m300`.
Instalator nakładek zachowuje `printer.cfg`. Do testu w osobnym katalogu
użyj ZIP dla Orange Pi z dołączonym `README_ZERO3_PL.md`.

Z `MCU/INVENTURE_RC8_0_SD_BOOT16.zip` wypakuj `INVENTUR.IVB` do głównego
katalogu FAT32 SD. W istniejącym InventureBoot sprawdź wybrany plik, wgraj
**samą aplikację** i wykonaj Verify Flash. Zachowaj poprzedni sprawdzony obraz
RC7.1 oraz backup hosta do wycofania. Bootloadera nie aktualizuj tym pakietem.

Po nowej aplikacji uruchom usługę Klippera. Bez wykonywania ruchu/grzania:

```gcode
INVENTURE_VERSION
INVENTURE_COOLING_STATUS
INVENTURE_STATUS
INVENTURE_OLED_STATUS
```

`INVENTURE_COOLING_STATUS` wysyła pojedyncze zapytanie i pokazuje ostatnią
otrzymaną próbkę z jej wiekiem. Po odpowiedzi kolejne wywołanie lub
`INVENTURE_STATUS` pokaże nowszy stan. Nie zwiększa stałego obciążenia UART.
Weryfikacja funkcji bez Orange Pi oraz prób gorącego shutdown wymaga
operatora przy drukarce i rzeczywistej kontroli temperatur/wyjść.

Wycofanie hosta, przy zatrzymanej usłudze:

```sh
python3 scripts/install_host.py ~/klipper --rollback '/dokładna/ścieżka/backup' \
  --confirm-service-stopped
```

Powrót MCU odbywa się przez osobne wgranie poprzedniego `INVENTUR.IVB` z
tego samego bootloadera. Instalator hosta nie zapisuje pamięci STM32.
