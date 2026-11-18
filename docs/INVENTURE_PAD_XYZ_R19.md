# Inventure — host R19: spójny status płyty podczas kalibracji

Host R19 poprawia odbiór i sprawdzanie statusu płyty w procedurach
serwisowych, w tym pełnej kalibracji pada. Wymaga dotychczasowego
MCU R12. **Obraz MCU nie zmienił się i nie wymaga ponownego wgrania,
jeżeli R12 jest już zainstalowane.**

## Wynik próby z klippy(15).log

Log zawiera poprzednie próby R16/R10 i R17/R11 oraz nową próbę
z MCU `RC8-CONTROL-R7-PAD-THERMAL-R12`, identyfikator kompilacji
`c335abb2a08a`. MCU zgłasza pasmo pomiarowe 90–110°C i timeout
komunikacji 5 sekund.

Najnowszy przebieg `1ecd153892a544049a1fe3b2ef43dfbd` wykonał
homing XY, referencję dolną Z, wybór T0, nagrzewanie i przetarcie.
Skan T0 doszedł przez krawędzie X i Y do kolejnego etapu `Y_TOUCH`.
Nie zakończył jeszcze pełnego pomiaru T0; T1 nie rozpoczęła skanu.

Przerwanie nastąpiło z komunikatem `Board/cable status is stale`.
Temperatury wynosiły T0 102,344°C i T1 100,894°C. Stan SERVICE miał
`fault=0`, `latched=0`; końcowy powód sesji 2 oznacza STOP hosta.
Ostatni zapis ruchu potwierdza styk PC14 po 1125 krokach Z.

Log nie zapisuje wieku próbki statusu, która uruchomiła zatrzymanie.
Nie pozwala więc rozstrzygnąć, czy był to opóźniony raport okresowy,
czy wyścig znaczników czasu. Sam komunikat nie potwierdza uszkodzenia
przewodu. Wcześniejszy błąd zapisu odpowiedzi do konsoli
`BlockingIOError` wystąpił przy homingu i nie przerwał tej procedury.

## Poprawka

Odbiór statusu odbywał się w wątku UART i mógł zmienić znacznik czasu
po odczytaniu zegara przez procedurę. Świeża próbka mogła wtedy dostać
ujemny obliczony wiek i zostać odrzucona jako nieaktualna.

- Status i jego oryginalny znacznik czasu są publikowane razem
  w wątku obsługującym procedury.
- Starszy odłożony raport nie zastępuje nowszego odczytu. Raport
  z poprzedniej generacji połączenia jest odrzucany po shutdown.
- Gdy raport okresowy przekroczy limit wieku, host najpierw wykonuje
  bezpośredni odczyt stanu płyty. Odczyt nie uruchamia ruchu ani grzania
  i nie odnawia ich zezwoleń. Subskrypcja raportów okresowych jest
  przywracana również przy błędzie odpowiedzi.
- Do kontynuacji potrzebna jest świeża, prawidłowa odpowiedź bez błędu
  płyty lub przewodu. Brak odpowiedzi, nieaktualna odpowiedź, fault,
  stan przewodu lub równoczesny STOP nadal zatrzymują procedurę.
- `details.board_status` zapisuje wiek i czas próbki, źródło,
  wcześniejszy wiek raportu okresowego oraz wynik odczytu.

Limit świeżości statusu pozostaje 0,75 s, a okres odpytywania 0,25 s.
Timeout utraty komunikacji w MCU nadal wynosi 5 s. Pasmo obu dysz
90–110°C, limity 310°C, globalna polityka drzwi, geometria ruchów
i rozdzielczość krawędzi 0,05 mm są takie jak w R18/R12.

## Weryfikacja

- 537 testów hosta, w tym publikacja statusu z wątku UART,
  kolejność raportów i wykluczenie próbek po shutdown.
- Pełny T0 → T1 → T0 na modelu drukarki z celowo opóźnionym
  statusem płyty w `Y_TOUCH` głowicy T0 kończy się `candidate`.
- Osobne testy potwierdzają zatrzymanie przy braku odpowiedzi,
  starym znaczniku czasu, fault, stanie przewodu i równoczesnym STOP.
- Sprawdzenie konfiguracji przez rzeczywistą przypiętą wersję Klippera.
- Obraz R12 zachowuje SHA256
  `5c890be7cccac852df31cab32b2db94d544ba34464377d61c34c798743828067`.

Są to testy programowe. Fizyczne zakończenie pełnego pomiaru
na drukarce pozostaje do potwierdzenia.

## Aktualizacja hosta i próba

SSH na Orange Pi:

```bash
git -C "$HOME/inventure-orange-pi" pull --ff-only origin orange-pi-all-axis &&
python3 "$HOME/inventure-orange-pi/install-orange-pi.py" --configure-nozzles
```

Jeżeli MCU R12 jest już zainstalowane, pozostaw je. Paczka MCU w tym
wydaniu służy instalacjom ze starszą wersją. Jej `INVENTUR.IVB` jest
identyczny z obrazem wydanym z hostem R18.

W Fluidd:

```gcode
FIRMWARE_RESTART
INVENTURE_VERSION
```

Oczekiwane: host `...PAD-XYZ-R19`, MCU `...PAD-THERMAL-R12`
i `nozzle_pad_measurement_band_c: [90, 110]`.

Przy pustych dyszach powtórz dotychczasową komendę:

```gcode
INVENTURE_CONTROL MODE=SERVICE
ZORTRAX_NOZZLE_PAD_CALIBRATE ACTION=START MODE=FULL SCAN=SWEEP PREP=EMPTY ACK=PAD_GEOMETRY_MEASURED T0_PAD_X=81.70 T0_PAD_Y=68.20 T1_PAD_X=70.80 T1_PAD_Y=68.70 T0_Z_MAX=139.50 T1_Z_MAX=137.95 PROCEDURE_TIMEOUT=3600
ZORTRAX_NOZZLE_PAD_CALIBRATE ACTION=STATUS
```

Oczekiwany wynik końcowy: `candidate` z pomiarami `T0`, `T1`
i `T0_RETURN`. Zapis wymaga osobnego `SAVE` po ocenie wyniku.
