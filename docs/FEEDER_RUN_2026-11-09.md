# Dłuższa próba podajnika kasety — FEEDRUN

`RC8-CONTROL-R5-FEEDRUN` dotyczy hosta Orange Pi. Korzysta z istniejącego
MCU CONTROL-R5 i jego komendy `inventure_feeder` z limitem 1500 ms na żądanie.
Nie zmienia firmware MCU, parametrów elektrycznych napędu, geometrii ani UART 750000.

## Co wykazała próba o 13:58:55

Użytkownik potwierdził nowy host SENSORS, SERVICE bez błędu i działający
odczyt rejestrów wszystkich portów. Baza: T0_A/B=0/0, T1_A/B=0/0,
liczniki PB12/PD14=2/1, poziomy PB12=1, PD14=0. Kaseta była zamontowana,
przycisk dociskany, a wylot rurki odłączony od głowicy.

Po poleceniu T0/DIR1/1500 ms użytkownik zaobserwował wysunięcie około 2 mm.
Potwierdza to podawanie lewym kanałem w DIR1. To przybliżona obserwacja,
nie kalibracja prędkości ani impulsów na milimetr.

Końcowe `INVENTURE_CASSETTE_DIAG ACTION=COMPARE` zostało odrzucone z powodu
aktywnego właściciela MCU. Nie ma więc końcowego odczytu tej próby i nie
można twierdzić, że liczniki po niej nadal wynosiły 2/1.

## Przyczyna przedwczesnego porównania

Pinned Klipper `ToolHeadGCode.cmd_G4` wywołuje `toolhead.dwell(delay)`.
`dwell` przesuwa czas kolejki ruchu i wywołuje `_check_pause`; nie gwarantuje
upływu całej przerwy w czasie rzeczywistym przed wykonaniem następnego
handlera serwisowego. Przy małej kolejce `G4 P1800` może od razu wrócić.
Podajnik działa natomiast na własnym zegarze MCU, poza kolejką XYZ/E.

Nowy host pamięta koniec ostatniego krótkiego okna podajnika. Przed
porównaniem wejść czeka według zegara monotonicznego i potwierdza stan idle
w odpowiedzi MCU. Oczekiwanie ma dodatkowy limit 0,75 s po końcu okna;
utrzymujący się aktywny stan nadal kończy się błędem. Nie wyłącza się
kontroli właściciela ani nie odczytuje wyniku jako ukończonej próby podczas ruchu.

## Podawanie przez dłuższy czas jedną komendą

Po aktualizacji hosta, z kasetą zamontowaną i wolnym wylotem rurki:

```gcode
INVENTURE_CONTROL MODE=SERVICE
INVENTURE_DOOR_POLICY ENABLE=0
INVENTURE_FEEDER_RUN CHANNEL=0 DIRECTION=1 DURATION=150
```

`DURATION` podaje się w sekundach, zakres 0,001–600; domyślnie 10 s.
Kanał 0 to lewy, kanał 1 — prawy. Komenda wraca po rozpoczęciu zadania,
dzięki czemu można wykonywać STOP z konsoli. Co około 5 s pojawia się postęp.

Host dzieli czas na okna nie większe niż 1500 ms. Każde kolejne żądanie
wysyła dopiero po upływie poprzedniego okna i potwierdzeniu idle przez MCU.
Suma zaprogramowanych okien wynosi żądany czas; cały test może trwać
nieco dłużej z powodu przerw na potwierdzenie. Firmware nadal sam kończy
każde okno, a watchdog heartbeat działa normalnie.

Przed startem host zapisuje świeży stan wejść i liczników. Po ostatnim
oknie sam wykonuje porównanie i zgłasza `V9_FEEDER_RUN phase=done`:

- `filament_before` / `filament_after` — A, B i liczniki przed/po;
- `pulse_delta` — różnica PB12/PD14;
- `changes` — zmienione wejścia GPIO;
- `completed_windows_s` — suma zakończonych zaprogramowanych okien MCU,
  nie pomiar odległości filamentu;
- `physical_motion_confirmed: false` — sam raport nie zastępuje obserwacji materiału.

Nie trzeba dopisywać `G4` ani ręcznego COMPARE za komendą RUN. Podczas
aktywnego zadania publiczne porównanie odrzuca się z jednoznacznym komunikatem.

Stan:

```gcode
INVENTURE_FEEDER_RUN ACTION=STATUS
```

Wcześniejsze zatrzymanie:

```gcode
INVENTURE_STOP
```

Można również użyć `INVENTURE_FEEDER_RUN ACTION=STOP`. Wspólny STOP,
STOP danego kanału, przejście OFF i shutdown usuwają zadanie hosta oraz jego
timer. Nie następuje ponowny start następnego okna. Po wcześniejszym STOP
świeży stan liczników można odczytać `INVENTURE_SENSORS` i porównać z
`filament_before` wydrukowanym na starcie.

Nowy właściciel operacji, aktywna procedura P13, błąd, utrata połączenia
lub opuszczenie SERVICE zatrzymują zadanie przed odnowieniem okna.
Kolejne próby serwisowego ruchu/wyjść i drugi RUN odrzuca się podczas zadania.
Zapytania timerów i konsoli są serializowane wspólnym mutexem, żeby nie
nadpisywały sobie callbacku oczekiwanej odpowiedzi UART.

## Oszacowanie 20 cm

Z obserwacji około 2 mm / 1,5 s wynika orientacyjnie:

```text
200 mm / (2 mm / 1,5 s) ≈ 150 s czasu podawania
```

To punkt wyjścia do próby. Po wysunięciu około 20 cm można zatrzymać ją
wcześniej. Nie zapisano tego oszacowania jako kalibracji napędu i nie
przypisano impulsom wartości mm. Grzałki oraz silniki głowicy pozostają
poza procedurą suchego transportu.

## Walidacja

84 testy hosta obejmują m.in.:

- odtworzenie dokładnego zgłoszonego skryptu z rzeczywistymi metodami G4/dwell
  pinned Klippera: poprzednia metoda daje zgłoszony błąd, nowa czeka do idle;
- 150 s jako 100 żądań po 1500 ms, bez zwiększenia limitu MCU;
- brak oczekiwania 150 s wewnątrz handlera G-code;
- automatyczny końcowy odczyt i różnicę liczników;
- wspólny STOP i brak późniejszego wznowienia, STOP kanału i shutdown;
- zmianę właściciela/P13, błąd, rozłączenie i timeout aktywnego MCU;
- nowy timer po ponownym starcie: stary callback nie może wznowić napędu;
- aktualizację dokładnego hosta SENSORS bez ingerencji w konfigurację MCU.

To walidacja oprogramowania. Fizyczna próba dłuższego transportu i kwalifikacja
PB12/PD14 oczekują na odczyty użytkownika.
