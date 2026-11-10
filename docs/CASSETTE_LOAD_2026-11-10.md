# Pierwsze wspólne ładowanie z lewej kasety — CASSETTE1

Host `RC8-CONTROL-R5-CASSETTE1`, istniejący MCU `RC8-CONTROL-R5`, bootloader
1.7, UART 750000. Aktualizacja obejmuje Orange Pi. Nie ma nowego wsadu MCU.

## Ustalony pomiar i odcinki

Użytkownik zgłosił około **90 mm od uchwycenia filamentu przez zębatkę E do
pierwszego wypływu**, po pięciu ruchach po 19000 kroków. Do odtworzenia tej
próby zapisano **95000 kroków**, bez zmiany dotychczasowych 960 kroków/mm.
To nominalnie 98,958 mm; przybliżona obserwacja 90 mm nie stanowi dokładnej
kalibracji skali. Nowy pomiar zastępuje wcześniejszy stan „wypływ niepotwierdzony”.

Log `klippy(1).log` potwierdza działanie LOAD15, 22 zakończone ruchy E w obu
kierunkach i pełne 900 s podtrzymania od pierwszej gotowości. Zakończenie:
`hold_duration_finished`, około 993,44 s od startu grzania. W późniejszych
próbach ZERO zerowano przed kolejnymi ruchami, więc ostatni FLOW zawiera
19000 kroków, a nie całą drogę od włożenia. Nie sumujemy ponownie tych samych
raportów. [Wyciąg z logu i obserwacja użytkownika](evidence/CASSETTE_LOAD_MEASUREMENT_2026-11-10.json).

Wcześniejsze **50 s → około 80 mm** dotyczyło ostatniego odcinka wejścia do
ekstrudera przy suchej próbie. Nie jest to zmierzona pełna długość rurki
od kasety do głowicy. Oba budżety są osobne:

| Etap | Parametr początkowy | Znaczenie |
|---|---|---|
| Podejście przez rurkę | `TUBE_SECONDS=50` | Odtworzenie poprzedniego czasu pracy kasety, około 80 mm roboczo |
| Od chwytu E do wypływu | `HEAD_STEPS=95000` | Pięć sprawdzonych odcinków po 19000 kroków; około 90 mm według pomiaru |
| Dodatkowe przepłukanie | `PURGE_STEPS=0` | Osobny, jawnie zadawany odcinek po dwóch powyższych etapach |

Początkowa suma to **171800 potwierdzanych kroków E**: 76800 na podejście
i 95000 na wnętrze głowicy. Oba silniki pracują do przodu w obu etapach;
przed dotarciem materiału z kasety E może obracać się bez filamentu.
Jeśli pusta jest cała rurka i droga jest dłuższa od poprzedniego ostatniego
odcinka, budżet podejścia wymaga korekty. Komenda nie ma niezależnego czujnika
dotarcia do dyszy i nie ogłasza automatycznie poprawnego załadowania.

## Aktualizacja i pierwsza próba

Orange Pi, przy zatrzymanej poprzedniej próbie:

```bash
git -C "$HOME/inventure-orange-pi" pull --ff-only && python3 "$HOME/inventure-orange-pi/install-orange-pi.py"
```

Włóż lewą kasetę, połącz rurkę z wejściem głowicy i pozostaw wybraną dyszę T0.
W konsoli Klippera:

```gcode
INVENTURE_VERSION
INVENTURE_CONTROL MODE=SERVICE
INVENTURE_DOOR_POLICY ENABLE=0
INVENTURE_LOAD_TEST ACTION=LOAD TOOL=T0 TARGET=230 DURATION=900 TUBE_SECONDS=50 HEAD_STEPS=95000
```

LOAD sam nagrzewa T0 i czeka na gotowość regulatora. Następnie uruchamia
lewą kasetę **DIRECTION=1** i główny E **DIR=0**. Nie trzeba wpisywać HEAT,
powtarzać pięciu JOG ani dodawać G4. Przed nowym LOAD aktywny poprzedni
LOAD/HEAT trzeba zakończyć `INVENTURE_STOP`.

Kaseta ma stałą częstotliwość napędu, dlatego dla pierwszej wspólnej próby
przyjęto nominalne 1,6 mm/s i **RATE=1536 kroków/s E**, zamiast samodzielnego
E przy RATE=4000. To wstępne dopasowanie prędkości do obserwacji 80 mm/50 s,
bez zmiany PWM ani częstotliwości kasety. Zakres RATE przy LOAD: 960–1536.
Przy niższym RATE skracane są impulsy kasety, aby jej przybliżony budżet
podawania odpowiadał odcinkowi E. Poślizg i rzeczywisty posuw obu napędów
pozostają do wspólnego pomiaru na drukarce.

Etapy działają asynchronicznie w oknach E o nominalnym czasie do 0,5 s,
z potwierdzeniem kierunków i liczby kroków MCU. Kaseta dostaje osobny impuls
do 500 ms po potwierdzeniu startu E. Przy domyślnym RATE pierwszy etap
sumuje 50 s żądanych impulsów kasety; cały program obejmuje około 111,849 s
żądanej pracy kasety plus przerwy na odpowiedzi i regulację. To różni się
od czasu całej operacji. Limit 50 s pojedynczej suchej próby pozostaje
w komendzie FEEDER_RUN; LOAD ma osobne, ograniczone budżety etapów.

Stan można odczytać w trakcie:

```gcode
INVENTURE_LOAD_TEST ACTION=STATUS
```

`cassette_load` pokazuje etap, `sent`, `total_steps`, żądany czas impulsów
kasety i `feed_completed`. Po `feed_done` oba napędy zatrzymują się,
regulator nadal trzyma temperaturę do końca 900 s od pierwszej gotowości,
a stan przechodzi w `awaiting_flow_confirmation`. Sam upływ czasu ani
licznik kroków nie potwierdza fizycznego wypływu. Po jego zaobserwowaniu:

```gcode
INVENTURE_LOAD_TEST ACTION=CONFIRM FLOW=1
```

Wspólny STOP w dowolnym momencie:

```gcode
INVENTURE_STOP
```

STOP wyłącza podawanie i grzanie, unieważnia właściciela przed kolejnym
żądaniem UART. Tak samo działa STOP dedykowany LOAD oraz STOP lewego
podajnika. Po zakończeniu próby użyj STOP, jeśli dalsze podtrzymanie nie jest potrzebne.

## Zakres tej próby i dalsze korekty

To etap transportu z kasety i potwierdzenia wypływu. Fabryczny pełny load
z audytu obejmuje też wybór narzędzia, ruchy serwisowe i czyszczenie. Obecna
próba zachowuje `cleaning_pending=true`, `position_established=false` oraz
`load_completed=false`; nie zastępuje kwalifikacji geometrii i tych etapów.
RFID i niezakwalifikowane wejście B nie są używane jako pozorne potwierdzenie
dotarcia do dyszy. A/B i PB12/PD14 są odczytywane przed i po podawaniu.

Do kolejnej próby można zmienić TUBE_SECONDS w zakresie 0–50, HEAD_STEPS
w zakresie 1–192000, PURGE_STEPS w zakresie 0–96000. Są to jawne, niezależne
budżety. Żaden dodatkowy purge nie jest automatycznie doliczany do pomiaru
90 mm. Na przykład `TUBE_SECONDS=0` służy próbie od już uchwyconego materiału.
Niedostateczny DURATION jest odrzucany przed rozpoczęciem grzania.

## Walidacja

142 testy hosta (w tym 11 testów wspólnego LOAD) obejmują czekanie na temperaturę, dokładne oddzielne budżety,
oba kierunki ładowania, brak kroków A/B/Z, wolniejsze E, wyłączanie kasety
podczas dogrzewania, wszystkie ścieżki STOP, utratę właściciela, kabla,
świeżych próbek i dzierżawy grzania oraz STOP podczas odpowiedzi START.

Uruchomiono też rzeczywisty kod CONTROL-R5 na makietach rejestrów: grzałka T0,
E i lewa kaseta pracują jednocześnie przy 960/1280/1536 Hz, timer kończy
impuls kasety, moc można odnowić między oknami E, brak odnowienia kończy
grzanie po 1500 ms. Wspólny STOP, brak heartbeat i utrata kabla zatrzymują
wszystkie trzy wyjścia. Osobny limit MCU odcina grzałkę; kontrola hosta
kończy wtedy także ruchy. [Dowód i zakres walidacji MCU](evidence/CASSETTE1_R5_JOINT_VALIDATION.json).

Są to testy oprogramowania i rejestrów. Pierwsza fizyczna próba jednoczesnego
podawania z kasety do nagrzanej głowicy pozostaje do wykonania na drukarce.
