# Inventure — komunikacja i płynne menu po transferze z M300

Wydanie hosta **RC8-CONTROL-R7-UIFAST**, 10 listopada 2026.
MCU pozostaje **RC8-CONTROL-R7-FACTORY**. Nie trzeba ponownie wgrywać IVB.

## Stan projektu i źródła

Przejrzano aktualną gałąź M300 `orange-pi`, commit
`c82d785736cf2241b4a4d0bf3328205a5a5b8076`, zbudowaną ze źródła
`6c359c8b301bf8dd3c0e3e4ec69382c6be324cad` (beta.21).
Podstawą transferu są `host/klippy/extras/m300_oled.py`,
`m300_oled_transport.py`, testy nawigacji i planowania okien oraz
opisy beta.16/beta.17. Identyfikatory źródeł zapisano w
[mapie transferu](evidence/M300_INVENTURE_UI_TRANSFER_SOURCE_2026-11-10.json).

| Mechanizm M300 | Zastosowanie w Inventure |
|---|---|
| Okna obrazu oceniane po kodowaniu | Porównanie planu obszarów z prostokątem obejmującym zmiany i podziałami przez pusty pas. Wygrywa mniejszy koszt pakietów bez zwiększania liczby operacji i danych SPI. |
| Jednoprzebiegowy PackBits | Najwyżej 32 bajty zakodowane i 256 odkodowanych, zgodnie z istniejącym ABI Inventure. Szum przechodzi na pakiety surowe. |
| Cztery kredyty DMA zwracane przez ACK | Kolejka pozostaje ograniczona. Callback ACK budzi nadawcę i następny termin reaktora. Nie odpytuje się co 2 ms tylko dlatego, że ACK jest w drodze. |
| Najnowszy widok zamiast kolejki obrazów | Jeden obraz wysyłany i jeden najnowszy oczekujący. Szybkie przewijanie nie odtwarza wszystkich pośrednich zaznaczeń. |
| Priorytet tła native serialqueue | Opcjonalne piksele korzystają z tego samego priorytetu co OLED M300. Własne kolejki oraz terminy ruchu/grzania zachowują pierwszeństwo. |
| Budżet pasma podczas pracy | Uwzględnia druk, natywnego właściciela, ruch serwisowy, podawanie i HEAT/JOG. Stan pracy sprawdzany najwyżej co 100 ms. |
| Tożsamość widoku przy odbiorze wejścia | Zachowany czas `#receive_time`, sesja i selekcja. Spóźniony OK nie wybiera nowego wiersza. Potwierdzenie musi dotyczyć obrazu zaakceptowanego przed odbiorem przycisku. |
| Potwierdzenie kompletnej ramki | Pozycja widoczna dla procedury dopiero po zakończeniu całego przesyłania. Generacje Inventure i jednorazowe tokeny pozostają obowiązujące. |
| Podgląd Fluidd beta.16–20 | Jest osobną funkcją frontendu M300. Ta aktualizacja optymalizuje panel fizyczny i jego sterownik Klippera; nie instaluje widgetu M300 ani nie zmienia Fluidd. |

Zachowano specyficzne dla Inventure: dwa narzędzia, przyciski UP/DOWN/OK/POWER,
64-bitową generację, 32-bitowe tokeny, zegary SPI2/DMA, układ pikseli,
inicjalizację SSD1322, kontrolę sesji i politykę lokalnego chłodzenia MCU.
Nie przenosi się mapy pinów, enkodera, zegarów ani pamięci bootloadera M300.
Prędkość UART pozostaje odczytywana z InventureBoot 1.7; instalator zachowuje
konfigurację hosta. Maksimum SPI pozostaje 3,75 MHz, a konkretna nastawa
`spi_speed` nie jest automatycznie podnoszona.

## Szybkie impulsy silników

M300 Classic wykonuje kwalifikowane bloki natywnego Klippera sprzętowo.
TIM2 i TIM3 generują ciągi STEP, a TIM4 i TIM5 zliczają impulsy i kończą
blok. Próba HW05 na M300 objęła 6054 ruchy na sucho przy 750000 baud.
Wydanie UIFAST przeniosło z M300 optymalizacje OLED i kolejkowania UART.
Generator kroków procedur kasetowych Inventure w tym wydaniu nadal
używał limitu 20 000 zdarzeń/s.
[Kod timerów M300](https://github.com/MarcinKowalik-AGH/Zortrax-m300-Klipper/blob/89c04d4f1c6a82b19f239b156958451e4d7fc764/mcu/SOURCE_MCU/overlay/src/stm32/m300_xy_step_timer.c) ·
[Zakres próby HW05](https://github.com/MarcinKowalik-AGH/Zortrax-m300-Klipper/blob/89c04d4f1c6a82b19f239b156958451e4d7fc764/docs/pl/PRINT_LIMITS_AND_PLANNER.md).

W Inventure profil `OEM` procedury T0 używa sprzętowych impulsów STEP
z TIM14, TIM13 i TIM11. Własna sesja SERVICE synchronizuje napędy A/B/E,
kontroluje czujniki kasety i zatrzymuje procedurę po utracie właściciela.
Stanowisko Inventure działa z `kinematics: none`. Natywna kolejka bloków
M300 oraz jej połączenia między timerami dotyczą płyty M300 Classic.
Na Inventure badanie prędkości zaczyna się od zimnego `SWEEP`.
[Profil T0, aktualizacja i polecenia](FACTORY_LOAD_2026-11-10.md).

## Poprawki Inventure

Przewijanie widoku procedury wcześniej ustawiało nowy kursor, lecz pomijało
flagę pilnego obrazu. Teraz również SELECT/PURGE/LOAD i pozostałe widoki
procedur budzą odświeżenie i omijają zwykły limit renderowania.

Dotychczasowy odstęp powtórzenia przy trzymaniu UP/DOWN wynosi około 100 ms
i pozostaje taki sam. Osobne krótkie naciśnięcia mają filtr 30 ms.
UP+DOWN nie przesuwa kursora i nie tworzy pętli z minionym terminem timera.
POWER i zatrzymanie mają dotychczasową obsługę.
Odrzucony OK pozostaje zablokowany do jego rzeczywistego puszczenia.
Równoczesna strzałka i OK przesuwa kursor, a potwierdzenie wymaga kolejnego
osobnego naciśnięcia po wyświetleniu wybranego wiersza.

Nie odtwarza się pakietu pikseli po utracie ACK. Udane późniejsze ACK
w tej samej kolejce FIFO może potwierdzić poprzednie operacje. Utrata
ostatniego ACK kończy sesję obrazu; istniejąca ograniczona procedura
odzyskania inicjalizuje ekran i pełną ramkę w nowej generacji. Błędne
ACK, stare generacje i przekroczone terminy nie zatwierdzają obrazu.

Budżet podczas pracy wynosi `host_baud * 0.020` bajtów/s, czyli 15000
przy 750000 baud. Koszt uwzględnia komendę, ACK, oba składniki generacji,
token i obwiednie protokołu. Szczyt kredytu to cztery pakiety po 84 bajty.
Jest to konserwatywny model; nie zastępuje pomiaru zajętości fizycznej linii.
Stan obrazu nie służy do potwierdzania fizycznego ruchu ani temperatury.

## Aktualizacja Orange Pi

Na bezczynnej drukarce:

```bash
git -C "$HOME/inventure-orange-pi" pull --ff-only && python3 "$HOME/inventure-orange-pi/install-orange-pi.py"
```

UIFAST korzysta z własnego modułu `inventure_oled_uifast_transport.py`.
Instalator sprawdza sumy plików i zapisuje kopie aktualizowanych modułów.
Poprzedni host przy wycofaniu aktualizacji ponownie korzysta ze swojego
`inventure_oled_transport.py`. Przewijanie i transmisję OLED można
sprawdzić również z MCU `RC8-CONTROL-R6-SMOOTH`.

W konsoli Klippera w Fluidd wykonaj polecenia

```gcode
INVENTURE_VERSION
INVENTURE_OLED_STATUS
```

Oczekiwane:

| Pole | Wartość |
|---|---|
| `host_revision` | `RC8-CONTROL-R7-UIFAST` |
| `mcu_release` | dotychczasowe `RC8-CONTROL-R7-FACTORY` |
| `baud_match` | `true` |
| OLED `ready` | `true`, po zakończeniu inicjalizacji |
| `ui_transport.navigation_api` | `INVENTURE-NAV-1` |
| `ui_transport.mode` | `packbits_spi2_dma`, jeżeli MCU deklaruje fast ABI |
| `ui_transport.window` | `4` dla DMA |

Najpierw przejdź przez **Information**, przewiń kilkanaście pozycji
pojedynczymi naciśnięciami i trzymając UP/DOWN. Zatrzymaj kursor i sprawdź,
czy ekran pozostaje na ostatniej pozycji. Potem wykonaj ponownie
`INVENTURE_OLED_STATUS`. Porównuj przyrosty `ui_transport.mcu_dma.errors`,
`uart_buffer_stats.dropped` i `recovery_attempts`, a nie samą sumę historyczną.
`scheduler_ticks`, `coalesced_requests`, `optimized_frames`, `last_frame_ms`
i `frame_p95_ms` opisują hosta/transport; nie są pomiarem fizycznej reakcji.

Nowe tokeny i wymagania native P09/P10/P13/P15/P17 pozostają opisane w
[dokumentacji menu](ZORTRAX_MENU_PROCEDURES_2026-11-10.md).

## Weryfikacja i otwarta próba fizyczna

Testy hosta sprawdzają pełny import i konfigurację adaptera, prawdziwy
renderer OEM, rekonstrukcję RAM SSD1322, jednoprzebiegowe kodowanie,
pojemność DMA, bariery resetu, końcowe ACK, błędy i generacje, tysiąc zmian
bez rosnącej kolejki, przyciski odebrane przed ACK, terminy timera,
SERVICE i instalację z przywróceniem. Kodowanie testowane jest również
z rzeczywistą funkcją C `oled_fast_decode` wyciętą bez zmian z MCU R7
i skompilowaną przez GCC. Rozmiary pakietów sprawdza przypięty parser
Klippera i słownik wydanej aplikacji R7.

Model obejmuje **500 przejść kursora** w obu językach. Przy identycznych
pikselach i tym samym kodowaniu porównuje stały plan obszarów z wyborem
po kompresji. Wynik: **210728 → 194746 bajtów UART, mniej o 7,58%**;
28 przejść ma mniejszy koszt, pozostałe go nie zwiększają. To wyizolowany
zysk planowania, nie porównanie zmierzonej płynności dwóch drukarek.
[Raport](evidence/INVENTURE_OLED_UART_MODEL_2026-11-10.json).

```bash
python3 -m unittest discover -s tests -p 'test_orange_pi*.py' -v
python3 service_mcu/control_r6/TESTS/run_mcu_tests.py
python3 ci/benchmark_inventure_oled.py
```

Fizyczna płynność, licznik błędów UART i zachowanie podczas jednoczesnej
pracy drukarki wymagają próby na Inventure użytkownika. Wydanie nie deklaruje
ich jako już potwierdzonych. Stan menu/procedur R7 i parametryzacja pozostają
częścią tego samego projektu, a nie osobnym eksperymentalnym portem M300.
