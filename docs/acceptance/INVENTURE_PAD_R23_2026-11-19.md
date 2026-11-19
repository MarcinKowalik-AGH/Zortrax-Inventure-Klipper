# Inventure — potwierdzenie pełnej kalibracji pada, 19.11.2026

Fizyczny przebieg **T0 → T1 → T0_RETURN zakończył się poprawnie**.
Status użytkownika z 10:32:59: `phase=candidate`, `error=null`.

Identyfikator: `6fa661efce2343d9be8f294437391a65`.
Pełny odczyt: [RESULT.json](INVENTURE_PAD_R23_2026-11-19_RESULT.json).

## Źródło i zakres

- Załącznik użytkownika: `klippy(16).log(1).zip`.
- SHA256 ZIP: `ed8d2a625721767c098b1a433cdf10f8f62b37dceed0103e79cc2349270bd879`.
- MCU jawnie rozpoznane w logu: `RC8-CONTROL-R7-PAD-THERMAL-R13-58ea5b576f76`.
- Wpisy pionowego TAP zawierają `latched_z_steps`, zgodnie z nową
  kwalifikacją R23. Log nie zawiera wyniku `INVENTURE_VERSION` z dokładnym
  identyfikatorem hosta. Wydanie R23 odpowiada commitowi
  `dc4d0f11398fcbfa8558714ba67a916883bee165`.
- Zakres fizycznego potwierdzenia: FULL, PREP=EMPTY, SCAN=SWEEP,
  rzeczywista metoda pomiarowa wszystkich trzech próbek SWEEP+TAP,
  temperatura zadana obu dysz 100°C, dopuszczalne pasmo 90–110°C.
- Log pokazuje odrzucane starty SWEEP i pomiary pionowe. Sukces nie
  oznacza osobnego potwierdzenia wszystkich ślizgowych przejazdów SWEEP.
- Pełny przebieg trwał 2078,3 s, około 34 min 38 s.

## Wyniki

| Wielkość | T0 | T1 | T0_RETURN |
|---|---:|---:|---:|
| Środek X [mm] | 81,700000 | 70,750000 | 81,700000 |
| Środek Y [mm] | 68,362500 | 68,7953125 | 68,362500 |
| Elektryczny wymiar X [mm] | 7,578125 | 7,49921875 | 7,578125 |
| Elektryczny wymiar Y [mm] | 7,64765625 | 7,57578125 | 7,64765625 |
| Powtarzalność dopasowania XY [mm] | 0,0328125 | 0,0171875 | 0,0328125 |
| Z rozwarcia od PE12 [mm] | 139,408977556 | 137,788029925 | 139,428927681 |
| Rozrzut dwóch próbek Z [mm] | 0 | 0 | 0 |

Wszystkie 24 potwierdzone przedziały krawędzi mają szerokość
0,075–0,0796875 mm. To potwierdza fizyczną próbę z dopuszczalnym
przedziałem 0,1 mm. Nie jest to potwierdzenie osiągnięcia 0,05 mm.

Sześć próbek Z zaakceptowano w pierwszych podejściach. Tam, gdzie było
potrzebne odzyskanie rozwarcia, wykonano 4 dodatkowe kroki w dół,
w istniejącym limicie. Zerowy rozrzut par oznacza zgodne liczniki kroków,
nie dowodzi zerowej niepewności pomiaru.

Powrót T0: X=0, Y=0, Z=+0,019950125 mm względem pierwszego pomiaru.
Z mieści się w kryterium 0,05 mm. Próbki temperatur zapisane przy
początkach trzech pomiarów mieszczą się w zakresie 94,4469–101,8608°C.

## Kandydat i konwencje znaków

| Pole kandydata | Wartość [mm] |
|---|---:|
| `t1_offset_mm.X` | +10,950000 |
| `t1_offset_mm.Y` | −0,432812 |
| `t1_carriage_shift_mm.X` | −10,950000 |
| `t1_carriage_shift_mm.Y` | +0,432812 |
| `t1_lower_by_mm` | +1,630923 |

Różnica Z wykorzystuje średnią pierwszego i powrotnego pomiaru T0:
`(139,408977556 + 139,428927681)/2 − 137,788029925 = 1,630922693 mm`.
Pola offsetu i przesunięcia karetki mają przeciwne znaki z definicji.

## Co pozostaje osobnym etapem

- `saved_file=null`: dostarczony status nie potwierdza zapisu.
- PREP=EMPTY: wykonane kroki E dla T0 i T1 wynoszą zero. Ten przebieg
  nie jest testem gorącej retrakcji ani załadunku filamentu.
- Zapis nie stosuje automatycznie offsetu do natywnej zmiany narzędzia
  podczas druku; taka integracja pozostaje osobnym zadaniem.
- Dla 0,05 mm potrzebna jest osobna próba fizyczna. Nie jest wymagana
  do uznania zakończonej próby z przedziałem 0,1 mm.

Log zawiera również nieblokujący błąd wyjścia konsoli `os.write` /
`BlockingIOError` w trakcie nowej próby. Procedura kontynuowała pracę
i zakończyła się powodzeniem. Starsze komunikaty o utracie MCU należą
do wcześniejszej historii; nie są powodem zatrzymania tego przebiegu.

## Zapis zatwierdzonego wyniku

Jeśli kandydat nadal znajduje się w bieżącej sesji hosta:

```gcode
ZORTRAX_NOZZLE_PAD_CALIBRATE ACTION=SAVE RUN_ID=6fa661efce2343d9be8f294437391a65 ACK=PAD_RESULT_REVIEWED
ZORTRAX_NOZZLE_PAD_CALIBRATE ACTION=STATUS
```

Oczekiwany odczyt po zapisie: `phase=saved` oraz ścieżka `saved_file`
do `inventure_nozzle_pad.json` obok głównego pliku konfiguracji Klippera.
Nie wymaga to ponownego pomiaru ani zmiany limitów.
