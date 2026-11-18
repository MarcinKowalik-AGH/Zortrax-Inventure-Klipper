# Inventure — host R22 i MCU R13: SWEEP z przejściem do TAP

R22/R13 poprawiają zakończenie niepotwierdzonego przejazdu po padzie.
Zmiana wymaga aktualizacji hosta i aplikacji MCU.

## Wynik ostatniej próby R21/R12

Załącznik `klippy(14).log(1).zip`, przebieg
`e8cea71f2d544cafb5da5c7430fe6b8d`:

| Pomiar | T0 | T1 |
|---|---:|---:|
| Środek X [mm] | 81,696875 | 70,7390625 |
| Środek Y [mm] | 68,41875 | 68,821875 |
| Powtarzalność środka XY [mm] | 0,0140625 | 0,0125 |
| Z rozwarcia od PE12 [mm] | 139,4033666 | 137,7830424 |
| Rozrzut dwóch próbek Z [mm] | 0,00374065 | 0 |

Obie głowice zakończyły pomiar XYZ. Procedura zatrzymała się dopiero
w `XYZ_SCAN_T0_RETURN`. Nie ma kompletnego wyniku do zapisania.

Zatrzymany ruch SWEEP miał `maximum_time`, 25,000 s, plan po 3200 kroków
na A i B i wykonane po 1078 kroków na obu silnikach. Z i E nie ruszały się.
Nie zarejestrowano potwierdzonego rozwarcia (`hit=0`). Ostatni bieżący
odczyt PC14 nadal wskazywał zwarcie. Przy chwilowym rozwarciu MCU zatrzymuje
XY i przez 150 ms sprawdza sygnał; ponowne zwarcie wznawia przejazd.
W logu długie postoje między przyrostami liczników zużyły limit czasu.

Temperatury przy zatrzymaniu wynosiły T0 100,5714°C, T1 100,0879°C;
status płyty miał wiek 0,116 s, `fault=0`, `latched=0`, kabel PD11=0.
Bezpośrednim powodem zatrzymania był limit czasu ruchu. W R12 oznaczał
on błąd całej sesji, więc istniejący pomiar TAP nie mógł się rozpocząć.

## Poprawka

- MCU R13 zatrzymuje przejazd w dotychczasowym limicie czasu. Nie zwiększa
  maksymalnego czasu ani odległości SWEEP.
- Wyłącznie nadzorowany SWEEP pada, z prawidłową sesją, temperaturami,
  ADC, tacką, krańcówkami, polityką drzwi, kablem i heartbeat, może zakończyć
  się wynikiem `pad_sweep_unqualified` zamiast globalnego błędu.
- STEP jest wyłączony, liczniki pozostają zamrożone, silniki zachowują
  pozycję, a wcześniejsza kwalifikacja styku zostaje zużyta. Nie wolno
  rozpocząć kolejnego SWEEP bez nowego pionowego dotknięcia.
- Host sprawdza tożsamość ruchu, równe liczniki A/B, kierunki, brak ruchu
  Z/E oraz brak trafienia wejść. Aktualizuje XY według rzeczywiście
  wykonanych kroków. Nie uznaje tego miejsca za krawędź.
- Następnie opuszcza stół do zweryfikowanej płaszczyzny przejazdu i mierzy
  obie krawędzie danej osi pionowymi dotknięciami TAP. Pomiar nie jest
  zatwierdzany na podstawie niepełnego SWEEP.
- Zwykłe timeouty, utrata sesji lub łączności, błędy temperatury i wejść
  oraz publiczny STOP nadal przerywają całą procedurę i wyłączają grzałki.
- Host R22 wymaga `nozzle_pad_sweep_timeout_abi: 1` przed ciepłym SWEEP,
  aby nie uruchamiać ponownie tej znanej ścieżki na R12. Starszy zimny
  `ACTION=CENTER` i tryb TAP zachowują dotychczasową zgodność.

Pozostają: pomiar 100°C ±10°C, limit grzania 310°C dla obu dysz,
heartbeat 5 s, domyślny odjazd 1 mm, dokładność domknięcia krawędzi
0,05 mm, osobne ograniczenia Z i globalne ustawienie drzwi.

## Weryfikacja

- Test hosta odtwarza końcowy powrót T0: po pełnych pomiarach T0 i T1
  SWEEP X+ kończy się przy 1078/3200 kroków po 25 s. Host przechodzi
  do TAP i otrzymuje pełny, powtarzalny kandydat T0 → T1 → T0.
- Test kodu MCU wykonuje krótkie rozwarcia i zwarcia wejścia PC14.
  Potwierdza 1078 zamrożonych kroków, brak dodatkowego ruchu, utrzymanie
  sesji i możliwość opuszczenia stołu oraz pionowego pomiaru.
- Sprawdzono oba źródła zatrzymania: callback STEP i niezależny sampler.
- Równoczesne błędy drzwi, tacki, krańcówek, temperatury, świeżości ADC,
  kabla, lease, feederów i grzałek oraz heartbeat nadal kończą sesję.
- Pełny zestaw: 544 testy hosta, 8 programów testujących rzeczywisty kod C
  na modelu rejestrów oraz uruchomienie konfiguracji przez przypięty Klipper.
- Paczka jest budowana GNU Arm 14.2.1; weryfikowane są słownik protokołu,
  nowy ABI, źródła, nagłówek IVB i SHA256 aplikacji.

Pełny fizyczny przebieg R22/R13 pozostaje do sprawdzenia na drukarce.
Testy programowe nie zastępują tego potwierdzenia. Wynik `candidate`
nadal wymaga oceny przed SAVE; nie jest automatycznie stosowany jako
offset natywnej zmiany narzędzia podczas druku.

## Aktualizacja

1. Pobierz `INVENTURE_MCU_PAD_SWEEP_R13_BOOT17.zip` z tego wydania.
   Sprawdź `SHA256SUMS`, rozpakuj i umieść `INVENTUR.IVB` w katalogu
   głównym karty SD FAT32. Wgraj aplikację przez InventureBoot 1.7.
2. W SSH na Orange Pi wykonaj:

```bash
git -C "$HOME/inventure-orange-pi" pull --ff-only origin orange-pi-all-axis &&
python3 "$HOME/inventure-orange-pi/install-orange-pi.py" --configure-nozzles
```

Po ponownym połączeniu sprawdź `INVENTURE_VERSION`: host
`RC8-CONTROL-R7-UIFAST-PAD-XYZ-R22`, MCU
`RC8-CONTROL-R7-PAD-THERMAL-R13`, `baud_match: true`,
`nozzle_pad_sweep_timeout_abi: 1`, pasmo `[90,110]`, heartbeat 5 s.
P4 pozostaje pobierane z ustawień bootloadera, bez wymuszania prędkości.

## Próba przy pustych dyszach

```gcode
INVENTURE_CONTROL MODE=SERVICE
ZORTRAX_NOZZLE_PAD_CALIBRATE ACTION=START MODE=FULL SCAN=SWEEP PREP=EMPTY ACK=PAD_GEOMETRY_MEASURED T0_PAD_X=81.70 T0_PAD_Y=68.20 T1_PAD_X=70.80 T1_PAD_Y=68.70 T0_Z_MAX=139.50 T1_Z_MAX=137.95 Z_CLEARANCE_MM=1 PROCEDURE_TIMEOUT=3600
```

Odczyt końcowego wyniku:

```gcode
ZORTRAX_NOZZLE_PAD_CALIBRATE ACTION=STATUS
```

Wynik `candidate` musi zawierać pomiary `T0`, `T1` i `T0_RETURN`.
Limity Z pozostają takie jak w poprzednich próbach; nie zwiększaj ich
na podstawie samego zatrzymania SWEEP.
