# Inventure R23 — lokalny OLED MCU według działającego M300

Firmware: `RC8-CONTROL-R7-HOMING-R23-OLED`.
Stan: **skompilowany i testowany programowo; próby fizyczne Inventure oczekują**.

Referencja: `Zortrax-m300-Klipper`, źródło wydania M300 1.0.8,
commit `20115e6b171c74a12a9578a3c95ec488c8ff6dd8`.
Dokładne ścieżki i SHA256 skopiowanych zasobów znajdują się w
`validation/INVENTURE_R23_M300_DISPLAY_PROVENANCE.json`.

## Obraz i komunikaty

- Oryginalny bitmapowy napis Zortrax, w wymiarach M300, jest wyświetlany
  przez 2,5 s tylko po uruchomieniu aplikacji. Host lub błąd przerywa go.
- Cztery klatki ikon oczekiwania, zerwanego połączenia i błędów pochodzą
  bez zmian z M300. MCU zachowuje wszystkie 16 odcieni 4bpp, obwiednię
  50×50 px, pozycję x=200/y=7 oraz jasność separatora i tekstu.
- Zostaje stockowa czcionka Inventure. Wszystkie trzy linie katalogu
  mieszczą się w lewym polu, poza ikoną. Test mierzy ich rzeczywistą szerokość.
- Oczekiwanie pokazuje `ZORTRAX INVENTURE`, `Waiting for Klipper...`,
  R23, wykrytą wersję bootloadera oraz rzeczywistą prędkość P4/8N1.
- Numeracja OEM pozostaje właściwa dla Inventure: potwierdzony kod kabla
  głowicy to #138. Inventure nie dostaje numerów platform M300 030/031.

| Zdarzenie | Tytuł i wskazówki na OLED |
|---|---|
| Utrata hosta/heartbeat | `Klipper link lost`; `Check host and UART`; `No automatic resume` |
| Timer | `Timer too close`; `Check timing / link`; `Read console / klippy.log` |
| Kroki | `Stepper timing error`; log Klippera i wymagany restart |
| ADC/temperatura | `Temperature ADC fault`; czujniki/okablowanie i log |
| Awaryjne zatrzymanie | `Emergency stop`; grzanie/ruch zatrzymane i wymagany restart |
| Kabel głowicy | `Error #138`; `Extruder cable error`; kabel i złącza |
| Wentylator | `Cooling fan feedback`; sprawdzenie wentylatorów głowicy/komory |
| Drzwi/pokrywa | `Door or lid open`; zamknięcie i usunięcie przyczyny |
| Tacka PC13 | `Build tray Z interlock`; osadzenie tacki i wymagany restart |
| Referencja Z | `Z reference failed`; dysza/pad i log |
| SPI/DMA wyświetlacza | `OLED transfer failed`; przewody OLED i reset MCU |
| Pozostałe zatrzymania | `Klipper shutdown` lub `MCU guard latched`; log i usunięcie przyczyny |

Nie ma automatycznego wznowienia druku po komunikacie połączenia lub błędu.

## Własność i błędy

MCU odbiera lokalną własność hostowi po 1500 ms bez ważnego ruchu
protokołu OLED, jak w M300. Stara generacja i jej ping nie mogą jej
przywrócić. Nowa sesja po zwykłej utracie łączności jest pełną transakcją
inicjalizacji. Zatrzaśnięte zatrzymanie MCU pozostaje nadrzędne.

Przyczyna Klippera jest zapisywana **przed** callbackami shutdown.
Ogólny kod 255 ustawiany później przez płytę nie może zamienić rzeczywistego
`Timer too close` na ogólne `Klipper shutdown`. Szczegółowy fizyczny interlock
PC13/PD11/temperatury ma własny opis. Kolejne konsekwencje nie zastępują
pierwszego zatrzymania. Nowe odczytowe polecenie protokołu MCU
`inventure_display_error_query` raportuje kod, rodzaj, fatalność,
identyfikator native i rewizję; jest dostępne także w shutdown.
To polecenie niskiego poziomu MCU, a nie nowo zarejestrowane makro G-code.

Callbacki błędu oznaczają pracę do wykonania. Rysowanie, czekanie na SPI
i animacje wykonuje ograniczony task. Osobny callback po resecie listy
timerów rozróżnia go od wcześniejszego wywołania awaryjnego płyty.

Oba tory OLED — DMA i starszy transport — mają ograniczony czas operacji.
Brak odpowiedzi SPI prowadzi do lokalnego błędu wyświetlacza, bez nowego
globalnego shutdown grzania/ruchu. Taki błąd transportu pozostaje zatrzaśnięty
do resetu MCU; próba odzyskania panelu nie kasuje go. Nie zmienia to
fizycznych zabezpieczeń, limitów temperatur i autonomicznego chłodzenia.

## Transmisja i pamięć

- Animacja jest lokalna: nie przesyła ramek ani potwierdzeń animacji przez UART.
- Statyczny tekst pozostaje w RAM SSD1322. Kolejna klatka zapisuje tylko
  wyrównany obszar 52×50 px: **1300 B danych + 7 B adresowania = 1307 B**.
  Pełna ramka wymaga 8199 B: spadek o **84,1%** na odświeżenie animacji.
- Dwie klatki na sekundę, jedna 128-bajtowa linia w RAM MCU, bez nowego
  pełnego bufora 8192 B i bez alokacji na stercie.
- Podczas statycznego oczekiwania timer śpi do następnej klatki. Renderowanie
  działa także przed konfiguracją hosta i po shutdown, bez zależności od pokrętła.
- Dotychczasowe cztery sloty DMA1 Stream4/channel0, generacje, tokeny,
  kompresja PackBits i regiony różnicowe hosta pozostają kompatybilne.
- Rozpakowywanie powtórzeń korzysta z tej samej optymalizacji Thumb-2 co
  M300. Test 2048 kombinacji długości, wyrównania i bajtów sprawdza równoważność.
  Dla serii 32/64/128 B emulowana liczba instrukcji jest mniejsza; to nie pomiar
  cykli rzeczywistego STM32 ani szybkości fizycznego UART.

Układ GPIO, zegary F207, wybór prędkości InventureBoot i bezpieczeństwo
Inventure nie są zastępowane parametrami F103 z M300.

## Instalacja i weryfikacja

R23 zachowuje ABI ruchu, harmonogram i rozdział SERVICE/NATIVE z R21/R22.
Dotychczasowy host R22 obsługuje niezmieniony protokół OLED. Zaktualizowany
`install-new-host.py` rozpoznaje R23 i sprawdza jego możliwości oraz wszystkie
wymagane wcześniejsze ABI. Nie trzeba zmieniać pinu UART ani geometrii.

Paczka zawiera `INVENTUR.IVB`, `klipper.bin`, `klipper.dict`, raporty,
SHA256, pełne źródła w `SOURCE_R23.zip` oraz podglądy pochodzące
z rzeczywistego kodu C w modelu rejestrów.
Wgrywanym plikiem aplikacji jest **INVENTUR.IVB przez dotychczasowy
InventureBoot 1.7**. Ten pakiet nie zawiera aktualizacji bootloadera i nie
używa formatu fabrycznego `InventureUpdate`.

Próby programowe:

```bash
python3 -m unittest discover -s tests -v
python3 service_mcu/control_r6/TESTS/run_mcu_tests.py
python3 -m unittest discover -s tests -p test_inventure_display_r23.py -v
python3 ci/benchmark_inventure_oled_rle.py --output rle.json
```

Kompilacja wymaga GNU ARM 14.2.1. Workflow
`inventure-r23-display.yml` sprawdza źródła, słownik, IVB, zasoby pikselowe
i optymalizację ARM, a następnie publikuje artefakt kandydata.
`ci/package_inventure_r23.py` tworzy paczkę z zatwierdzonego commita,
porównując SHA256 bieżących źródeł z raportem kompilacji oraz sprawdzając
IVB, BIN, ABI i odczyt wszystkich plików z gotowego ZIP.

**Pozostałe próby fizyczne:** uruchomienie bez hosta, podłączenie hosta
podczas logo, rozłączenie i ponowne podłączenie, czytelność wszystkich
komunikatów, działanie menu oraz Home z aktywnym OLED. Awaryjne przypadki
elektryczne są testowane w modelu; nie należy odłączać czujników gorącej
drukarki w celu ich odtworzenia. R23 nie stanowi jeszcze potwierdzenia
usunięcia sprzętowego `Timer too close` z issue #3.
