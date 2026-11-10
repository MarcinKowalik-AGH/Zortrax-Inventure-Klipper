# LOAD15 — 15 minut podtrzymania i pomiar toru filamentu

Host `RC8-CONTROL-R5-LOAD15` korzysta z obecnego MCU `RC8-CONTROL-R5`.
Nie wymaga nowego wsadu ani zmian `printer.cfg`.

## Czas i grzanie w trakcie ruchu

Podtrzymanie domyślnie trwa **900 s od pierwszej gotowości**, z osobnym limitem
300 s na rozgrzewanie. Cała sesja ma najwyżej 1200 s. `DURATION=10..900`
wybiera czas podtrzymania. STATUS, ZERO, kolejne JOG ani ponowne uzyskanie
gotowości nie odnawiają tego czasu. Upływ podtrzymania kończy sesję z `done`.

R3 wyłączał grzałkę na cały JOG. Kilka ruchów po około 20 mm bez przerwy mogło
odebrać gotowość przez wychłodzenie. LOAD15 dzieli jeden JOG na okna do 0,5 s
nominalnego ruchu. PID ustawia moc przy bezczynnym silniku, a MCU utrzymuje ją
w trakcie krótkiego okna E. Po oknie host aktualizuje PID i wydaje następne.
Każde okno ma limit MCU do 750 ms, a grzałka dzierżawę najwyżej 1500 ms.

R5 pozwala rozpocząć E przy zwykłym PWM grzałki, lecz odrzuca odnowienie PWM
podczas ruchu. Sprawdzono to przez kompilację aktualnego C R5 z mockami
rejestrów dla 160, 600, 2000, 4000 i 4800 Hz.
[Dowód i źródła testu](evidence/LOAD15_R5_HEATED_WINDOWS_VALIDATION.json).
To test kodu, nie nowy firmware ani pomiar fizyczny.

Jeśli temperatura nie pozwala rozpocząć następnego okna, pozostały fragment
czeka w `reheating`, z działającym grzaniem. Po odzyskaniu gotowości kontynuuje
tylko pozostałe kroki. Czujniki, przewód, tachometr, połączenie, przegrzanie,
dzierżawa i właściciel są kontrolowane dalej. STOP anuluje też przyszłe okna.

Jeden JOG dopuszcza do 19200 kroków, 160..4800 Hz i do 10 s nominalnych
(`STEPS/RATE <= 10`). Rzeczywisty czas może być dłuższy wskutek ramp i przerw
na regulację. Limit całego JOG to `STEPS/RATE + liczba_okien*0.5 + 30` sekund.
Nie ma limitu trzech poleceń; następne wydaje się po `jog_done`.
Odczyt stanu i czterech sterowników jest serializowany jako jedna operacja,
aby nowe okno nie zmieniło numeru ruchu w środku innego odczytu statusu.

## Pomiar od chwycenia końcówki do wypływu

Po aktualizacji Orange Pi rozpocznij sesję dla testowanego T0:

```gcode
INVENTURE_CONTROL MODE=SERVICE
INVENTURE_DOOR_POLICY ENABLE=0
INVENTURE_LOAD_TEST ACTION=HEAT TOOL=T0 TARGET=230 DURATION=900
```

Po `ready_for_jog: true` trwa 15 minut podtrzymania. TOOL wybiera grzałkę,
nie przestawia mechanicznie narzędzia. Doprowadź końcówkę do chwycenia przez
zębatkę, a w tym punkcie wyzeruj pomiar:

```gcode
INVENTURE_LOAD_TEST ACTION=ZERO
```

ZERO nie wykonuje ruchu, nie zeruje pozycji MCU i nie odnawia czasu grzania.
Podawaj pojedyncze odcinki, czekając za każdym razem na `jog_done`:

```gcode
INVENTURE_LOAD_TEST ACTION=JOG DIR=0 STEPS=19000 RATE=4000
```

To nominalnie 19,792 mm. Dla cofania T0 wybierz DIR=1. Przy zbliżaniu do
wypływu użyj małych odcinków, np. 1 mm:

```gcode
INVENTURE_LOAD_TEST ACTION=JOG DIR=0 STEPS=960 RATE=1000
INVENTURE_LOAD_TEST ACTION=STATUS
```

W `measurement` są osobne `dir0_steps`, `dir1_steps`, nominalne `dir0_mm`,
`dir1_mm` i różnica `net_dir0_mm`. Dla T0 DIR=0 oznacza podawanie. Pole
`zeroed_by_operator` wskazuje wybranie punktu początkowego; `completed_jogs`
liczy ukończone JOG, a `has_unfinished_jog` wskazuje ruch niedokończony.
Suma pochodzi z kroków potwierdzonych przez MCU, nie z żądań kolejki.
Powtórzony odczyt tego samego licznika nie jest doliczany ponownie.

`motion` opisuje ostatnie okno MCU, a `last_jog` — łączny wynik ostatniego
polecenia. Przy przerwaniu w środku okna wynik opiera się na ostatnim
potwierdzonym odczycie. Pomiar końcowy wykonuj małymi, ukończonymi JOG.

Po pierwszym zauważonym wypływie i zakończeniu ostatniego małego ruchu:

```gcode
INVENTURE_LOAD_TEST ACTION=CONFIRM FLOW=1
```

Zapisuje to kopię pomiaru w `measurement.flow_mark`, zachowaną także po
dalszych ruchach. Nowe ZERO usuwa znacznik i zaczyna nowy pomiar. Nie jest to
potwierdzenie pełnego load, czyszczenia ani RFID. Sesję kończy `INVENTURE_STOP`.

Skala 960 kroków/mm jest nominalna; obserwacja 19000 kroków ≈20 mm jest z nią
zgodna. Poślizg i ściśnięcie materiału nie są mierzone. Dla fizycznego pomiaru
zaznacz filament przy wejściu i porównaj przesunięcie znacznika. Stary materiał
w kanale może wypłynąć wcześniej niż nowa końcówka i zmienić interpretację.

## Czy odległość wynika z fabrycznego load?

Audyt r0323 pokazuje dla zewnętrznego load `0x080149B0` budżet do 250 mm E,
zatrzymywany m.in. potwierdzeniem użytkownika. Początek ruchu nie jest sprzętowo
związany z chwilą chwycenia filamentu. W load kasety `0x0801433C` występują
również budżety feed/purge i próby transportu. Nie są wymiarem zębatka–dysza.
Nie można z samej liczby 250 mm wyliczyć szukanej odległości. Potrzebny jest
fizyczny punkt ZERO i obserwacja wypływu, z uwzględnieniem materiału w kanale.

Źródła: konsolidacja projektu z 2 października, sekcja „Ręczne ładowanie
external”, audyt cartridge load z 4 października i
[rozróżnienie liczników w audycie wejść](CASSETTE_START_AND_INPUTS_2026-11-09.md).
