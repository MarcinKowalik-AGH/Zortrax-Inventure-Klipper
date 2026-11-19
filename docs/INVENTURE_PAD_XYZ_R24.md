# Inventure R24 — audyt pełnej kalibracji pada

R24 jest aktualizacją hosta Orange Pi dla MCU R13. Pierwszy fizyczny wynik
serii pochodzi z R23. O 10:39:05 Klipper potwierdził zapis przebiegu
`6fa661efce2343d9be8f294437391a65`. Offset T1 wyniósł X +10,950000 mm
i Y −0,432812 mm, a T1 sięgała niżej o 1,630923 mm. Obie dysze były puste.
Dalsze próby na tej samej wersji i z tymi samymi parametrami posłużą do
obliczenia rozrzutu. Fizyczny odbiór optymalizacji R24 jest odrębną próbą.
[Wynik R23 i źródła](https://github.com/MarcinKowalik-AGH/Zortrax-Inventure-Klipper/blob/inventure-pad-xyz-r24/docs/acceptance/INVENTURE_PAD_R23_2026-11-19.md).

## Co pokazał log R23

| Obserwacja | Znaczenie |
|---|---|
| Koniec parkowania po 2138,084 s | Pełna procedura trwała 35 min 38 s. Wartość 2078,3 s w raporcie ogólnym liczono od rozpoczęcia grzania. |
| Trzy moduły skanu zajęły łącznie 1926,483 s | Około 90% przebiegu przypadło na pomiary T0, T1 i powrotnej T0. Każde osadzenie miało dwa własne przebiegi XY. |
| Wszystkie 12 pomiarów osi przeszło ze SWEEP na TAP. Log zawiera 180 podwójnych pomiarów pionowych | Wszystkie 24 końcowe krawędzie wyznaczyły pionowe dotknięcia TAP. Najszerszy potwierdzony przedział miał 0,0796875 mm przy nastawie 0,1 mm. |
| 4079 komunikatów `V9_MOTION` i 482 komunikaty `V9_LOAD_TEST` | Od pierwszego statusu `running` do `candidate` te dwa typy zajęły razem 8 017 200 bajtów w logu. W tym czasie wystąpił jeden `BlockingIOError` przy zapisie odpowiedzi do konsoli. |
| Ogólny limit procedury wynosił domyślnie 1800 s | Wykonany pomiar miał jawny limit 3600 s. Domyślne 30 minut nie wystarczyłoby na ten przebieg. |

## Zmiany w hoście

- W drugim przebiegu osi pierwszy wynik podpowiada, które punkty podziału
  sprawdzić wcześniej. Host kwalifikuje styk w bieżącym punkcie wewnętrznym
  i rozwarcie w zewnętrznym. Sprawdza też zewnętrzny koniec zakresu.
  Niepewna lub przesunięta krawędź uruchamia zwykłe szukanie w całym
  potwierdzonym przedziale. Każdy punkt wymaga dwóch pomiarów pionowych.
  T1 i powrotna T0 rozpoczynają własne pomiary od zadanego środka.
- Samodzielny `SCAN=TAP` wykonywał dwa przebiegi XY, lecz pomijał porównanie
  ich środków. R24 sprawdza `XY_REPEAT_MM` przed pomiarem Z. Pole
  `edge_sources` wskazuje dla każdej osi źródło końcowych krawędzi w obu
  przebiegach. Dotychczasowe `scan_method` pozostaje dostępne.
- Pełna kalibracja dostaje domyślnie 3600 s. Profil i parametr polecenia
  mają pierwszeństwo. `VERIFY_T0` i `CENTER` mają domyślnie 1800 s.
  Te wartości dotyczą całej procedury hosta. MCU nadal samodzielnie
  zatrzymuje ruch i grzanie według swoich limitów.
- W czasie pomiaru host wysyła do konsoli krótki `V9_NOZZLE_PAD_PROGRESS`
  z etapem, dyszą, temperaturami, numerem próby i czasem od startu. Pełny
  raport mechaniki trafia do `klippy.log` przy zakończeniu. Na żądanie
  jest dostępny przez `INVENTURE_LOAD_TEST ACTION=STATUS`. Host nadal
  odczytuje ruch i wyjścia MCU. Wpływ na błąd zapisu konsoli wymaga obserwacji na drukarce.
- `ACTION=STOP` korzysta z tej samej obsługi co `INVENTURE_STOP`.
  Raport pada i raport serwisowy podają zgodny powód zatrzymania przez operatora.
  Przy błędzie zostają już pobrane próbki temperatur.
- `STATUS` i zatwierdzony `SAVE` zawierają parametry pomiaru, wersje hosta
  i MCU oraz czas całej procedury. `SAVE` zapisuje wynik do pliku konfiguracji
  `inventure_nozzle_pad.json`. Kolejny zatwierdzony zapis zastępuje ten plik.

## Porównanie na tym samym modelu ruchu

Symulacja wykonuje pełne T0 → T1 → T0 przy 100°C z pustymi dyszami.
Środki modelu pochodzą z fizycznego wyniku R23. Model ma okrągły pad
o promieniu elektrycznym 3,8 mm, stałe wysokości kontaktu 111806 i 110506
kroków oraz odmowy startu ślizgowego SWEEP. Porównanie obejmuje zatem
ścieżkę pionową dominującą w logu. Podany czas pochodzi z zegara modelu.

| Maksymalny przedział | Punkty podwójnie mierzone R23 → R24 | Czas modelu R23 → R24 | Różnica |
|---|---:|---:|---:|
| 0,10 mm | 180 → 160 | 1919,5 → 1749,5 s | 170 s (8,9%) |
| 0,05 mm | 204 → 173 | 2117 → 1855 s | 262 s (12,4%) |

Przy każdej z obu tolerancji wszystkie krawędzie i offsety w modelu są
identyczne między wersjami. Testy obejmują przesunięcie rzeczywistej
krawędzi względem poprzedniego przebiegu, niejednoznaczny styk, utratę
temperatury, przerwanie przez operatora i zapis kompletnego wyniku.
Dodatkowe porównanie objęło 12 800 przypadków krawędzi o pojedynczym
przejściu styk–rozwarcie w obu kierunkach. W każdym przypadku węższe
szukanie dało ten sam wynik co przeszukanie pierwotnego zakresu.
Weryfikacja obejmuje 555 testów hosta, osiem programów testowych MCU i
wczytanie konfiguracji obu grzałek w dołączonej wersji Klippera.

## Instalacja po zakończeniu serii porównawczej R23

Przy działającym MCU R13 zaktualizuj host w SSH na Orange Pi po zakończeniu próby.

```bash
git -C "$HOME/inventure-orange-pi" pull --ff-only origin orange-pi-all-axis &&
python3 "$HOME/inventure-orange-pi/install-orange-pi.py" --configure-nozzles
```

`INVENTURE_VERSION` powinno podać host `RC8-CONTROL-R7-UIFAST-PAD-XYZ-R24`
i MCU `RC8-CONTROL-R7-PAD-THERMAL-R13`. SHA256 aplikacji `INVENTUR.IVB`
wynosi `7bb8a9334d2aade7eb38f4d949d73f2e10870cde4b12e3c8803ba7cdff0b8f35`.
Pierwszą próbę fizyczną R24 można porównać z R23 przy identycznym poleceniu.

```gcode
INVENTURE_CONTROL MODE=SERVICE
ZORTRAX_NOZZLE_PAD_CALIBRATE ACTION=START MODE=FULL SCAN=SWEEP PREP=EMPTY ACK=PAD_GEOMETRY_MEASURED T0_PAD_X=81.70 T0_PAD_Y=68.20 T1_PAD_X=70.80 T1_PAD_Y=68.70 T0_Z_MAX=139.50 T1_Z_MAX=137.95 Z_CLEARANCE_MM=1 EDGE_TOLERANCE_MM=0.1 PROCEDURE_TIMEOUT=3600
```
