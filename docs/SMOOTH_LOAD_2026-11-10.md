# Ciągłe ładowanie — CONTROL-R6-SMOOTH

## Wynik próby CASSETTE1

Użytkownik potwierdził fizyczne załadowanie z lewej kasety do T0. Nowy log
`klippy(2).log` potwierdza pełne 171800/171800 kroków, 224 okna,
150,005 s podawania, T0 226,608–230,476°C podczas ruchu, PB12 +183
i PD14 +1. Następnie zgłoszono `CONFIRM FLOW=1`.
To potwierdzenie współpracy obu napędów i wypływu; liczniki nadal nie są
skalibrowanym pomiarem długości. [Wyciąg z logu](evidence/CASSETTE1_PHYSICAL_LOAD_2026-11-10.json).

Zatrzymania wynikały z dzielenia E na okna po około 0,5 s. CONTROL-R5
odrzuca zmianę mocy grzałki podczas ruchu E. Dlatego host kończył okno,
odnawiał moc i rozpoczynał następny odcinek. Każdy odcinek ponownie
włączał sterownik i przechodził rampę prędkości. Samo przyspieszenie
odpytywania UART nie zapewniłoby ciągłego ruchu i regulacji temperatury.

## Zmiana w MCU i hoście

Nowy MCU `RC8-CONTROL-R6-SMOOTH` ma osobną zdolność
`INVENTURE_LOAD_STREAM_ABI=1`. Cały program E jest jednym ruchem,
a wybrany podajnik kasety pozostaje włączony. Odnowienie nadzoru zmienia
wyłącznie termin ważności; nie zeruje licznika E, nie ustawia ponownie
kierunku i nie przełącza RUN. PID może aktualizować moc wybranej dyszy
podczas tego ruchu. Pozostałe próby i wyjścia zachowują swoje ograniczenia.

Host odnawia nadzór co około 0,25 s. MCU ma niezależną dzierżawę 1500 ms
dla wspólnego podawania, osobną dzierżawę mocy oraz kontrolę temperatury,
kabelka głowicy, heartbeat i polityki drzwi. Brak odnowienia zatrzymuje
ruchy i grzanie nawet wtedy, gdy inne zadanie hosta nadal wysyła heartbeat.
Duplikat START lub odnowienia nie rozpoczyna ponownie ruchu ani nie wydłuża
tej samej dzierżawy. Błąd wymaga nowej, jawnej próby.

MCU zachowuje wszystkie możliwości CONTROL-R5: A/B/Z/E, CoreXY, oba
podajniki, cztery grzałki, wentylatory, wejścia i czujniki. Nowy protokół
ciągłego podawania ma parametry wyboru dyszy, podajnika i obu kierunków,
żeby późniejsze kwalifikowanie drugiego toru wymagało zmian parametrów
hosta. Obecna procedura LOAD używa fizycznie potwierdzonego T0/lewego
toru: E DIR0, kaseta DIR1. Nie zmienia bootloadera ani wyboru UART.

## Instalacja

1. Zakończ aktywną próbę `INVENTURE_STOP`. Po schłodzeniu głowicy
   skopiuj z paczki `MCU/INVENTUR.IVB` na kartę i zainstaluj aplikację
   przez obecny InventureBoot 1.7.
2. Zaktualizuj host Orange Pi:

```bash
git -C "$HOME/inventure-orange-pi" pull --ff-only && python3 "$HOME/inventure-orange-pi/install-orange-pi.py"
```

3. W Klipperze:

```gcode
FIRMWARE_RESTART
```

Po ponownym połączeniu odczytaj `INVENTURE_VERSION`. Oczekiwane:
`host_revision` i `mcu_release` = `RC8-CONTROL-R6-SMOOTH`,
`load_stream_abi: 1`, `host_baud: 750000`, `p4_baud: 750000`,
`p4_forced_baud: 0`, `baud_match: true`.

## Próba, gdy filament jest już załadowany

Pozostaw kasetę i rurkę połączone z T0. Ten wariant podaje tylko około
20 mm nominalnie, bez ponownego odtwarzania całego pustego odcinka rurki:

```gcode
INVENTURE_CONTROL MODE=SERVICE
INVENTURE_DOOR_POLICY ENABLE=0
INVENTURE_LOAD_TEST ACTION=LOAD TOOL=T0 TARGET=230 DURATION=900 TUBE_SECONDS=0 HEAD_STEPS=19000 SMOOTH=1
```

Host sam nagrzewa dyszę i czeka na gotowość. Podczas podawania oba napędy
powinny pracować ciągle. Oczekiwane: `cassette_load.continuous_motion=true`,
jedno rozpoczęcie ruchu i `last_jog.windows=1`. Pola
`feeder_windows_requested` i `feeder_ms_requested` są w tym trybie `null`;
aktualny postęp pochodzi z `load_stream.sent` i `elapsed_ms` MCU.

Stan: `INVENTURE_LOAD_TEST ACTION=STATUS`.
Zatrzymanie napędów i grzania: `INVENTURE_STOP`.

Po zakończeniu transportu MCU zatrzymuje podajnik, a PID utrzymuje
temperaturę do końca 900 s od pierwszej gotowości. Po zaobserwowaniu
wypływu można wydać `INVENTURE_LOAD_TEST ACTION=CONFIRM FLOW=1`.
Etapy czyszczenia i kwalifikacji pełnej geometrii pozostają odrębne.

## Pełne ładowanie od dotychczasowego punktu początkowego

Budżet jest taki sam jak w zakończonej próbie:

```gcode
INVENTURE_LOAD_TEST ACTION=LOAD TOOL=T0 TARGET=230 DURATION=900 TUBE_SECONDS=50 HEAD_STEPS=95000 SMOOTH=1
```

Nie dodano ponownie 90 mm ani dodatkowego purge. Zachowano wcześniejsze
50 s podejścia oraz pięć razy 19000 kroków, łącznie 171800 kroków E.
Nominalne RATE=1536 odpowiada roboczemu dopasowaniu do kasety 1,6 mm/s.
Skala 960 kroków/mm i oszacowanie 90 mm nie zostały przeliczone na nową
kalibrację. Tryb ciągły używa RATE=1536; zmiana dopasowania prędkości
wymaga kolejnego pomiaru. Przebieg TUBE/HEAD/PURGE jest raportowany według
licznika, bez zatrzymywania na granicach etapów.

Przy `SMOOTH=1` host z MCU CONTROL-R5 zgłosi brak wymaganej funkcji przed
uruchomieniem grzania. `SMOOTH=0` zachowuje wcześniejszy tryb krótkich okien.

## Walidacja i jej zakres

- 152 testy hosta: zgodność poprzednich wersji, instalacja z CASSETTE1,
  regulacja temperatury, jeden START dla całego odcinka, potwierdzane kroki,
  STOP, wygaśnięcie nadzoru mimo działającego hosta, błędna własność,
  nieaktualne ADC, niewłaściwe odpowiedzi i słownik rzeczywiście zbudowanego MCU.
- Cztery programy testowe C wykonują faktyczny kod płyty, PWM i sterowania
  na makietach rejestrów. Obejmują regresję motorów, krańcówek, temperatur,
  chłodzenia, trybów R5 oraz nowy ciągły transport.
- Dla 171800 kroków przy 1536 Hz model rejestrów MCU zakończył jeden ruch
  w 111942 ms, z jedną rampą na początku i końcu. Największy odstęp STEP
  podczas ruchu wyniósł 2403 µs; brak przerw między dawnymi 224 sekcjami.
- Sprawdzono przejścia zegara przez zero, 447 odnowień bez restartu E,
  obie dysze/podajniki/kierunki, zachowanie wspólnego TIM9 przy PID T1,
  utratę kabla i heartbeat, wygasanie dzierżaw, temperatury, drzwi,
  shutdown oraz odrzucanie starych START/odnowień.
- GNU ARM 14.2.1: poprawny STM32F207, 120 MHz, start aplikacji 0x08010000,
  obszar aplikacji i RAM, wektory, spójne ELF/BIN/IVB, SHA-256 i osadzony
  słownik protokołu, odczyt ustawienia UART z bootloadera 1.7.

Czas 112 s pochodzi z testu kodu MCU. Pierwsza fizyczna próba R6 pozostaje
do wykonania; potwierdzona próba na drukarce dotyczy CASSETTE1.
