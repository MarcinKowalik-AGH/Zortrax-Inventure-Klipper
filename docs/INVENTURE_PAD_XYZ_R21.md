# Inventure — host R21: zapamiętany styk środka Z

R21 poprawia kwalifikację styku przy końcowym pomiarze Z na padzie.
Aktualizacja dotyczy hosta. MCU R12, odjazd 1 mm, ograniczone odzyskiwanie
rozwarcia i warunki powtarzalności pozostają takie jak w R20.

## Wynik próby z klippy(17).log

Ostatni przebieg: `b6f350489c9b4d95afbd68e25966f7e7`.

| Pomiar | T0 | T1 |
|---|---:|---:|
| Środek X [mm] | 81,678125 | 70,7640625 |
| Środek Y [mm] | 68,3859375 | 68,8203125 |
| Różnica środka XY między przejściami [mm] | 0,0296875 | 0,0234375 |
| Z od PE12 [mm] | 139,4133416 — dwie próbki | 137,7892768 — jedna próbka |
| Rozrzut dwóch próbek Z [mm] | 0,00374065 | brak drugiej próbki |

T0 wykonała pełny pomiar XYZ. T1 zakończyła XY i pierwszą próbkę Z.
Powrotny pomiar T0 jeszcze nie nastąpił, więc nie ma kompletnego
kandydata do zapisania ani zatwierdzonego offsetu obu dysz.

Druga próbka T1 zakończyła cztery próby na tej samej wysokości
110507 kroków. Każda miała `closed=false`, `missed=false`,
`release_hit=null`, `release_sent_steps=null`.
Oznacza to, że program odrzucał kwalifikację zetknięcia jeszcze
przed rozpoczęciem ruchu szukającego rozwarcia. Komunikat R20
`PC14 release was not repeatable at pad center` nie rozróżniał tych
dwóch etapów.

MCU rejestrowało styk PC14 podczas dojazdu, natomiast późniejszy
odczyt wejścia był już otwarty. W R20 środkowy pomiar wymagał także
utrzymania zwarcia przy późniejszym odczycie na postoju. Taki warunek
nie odpowiada obsłudze zapamiętanych zboczy w homingu Z.

Temperatury końcowe: T0 102,183°C, T1 102,505°C. Status płyty miał
wiek 0,255 s, `fault=0`, `latched=0`, `cable=0`. Procedurę zakończył
STOP hosta; nie wystąpiło zatrzymanie MCU przez guard termiczny.

## Poprawka

- Środkowy pomiar Z uznaje zwarcie zapamiętane przez MCU, jeżeli
  dojazd rzeczywiście wykonał dodatnią liczbę kroków i zatrzymał się
  na PC14. Późniejsze rozwarcie nie kasuje zdarzenia styku.
- Odczyt aktualnie zwartego wejścia nadal jest wymagany tam,
  gdzie potrzebuje go pomiar krawędzi XY. Ta kwalifikacja nie zmienia się.
- Po styku nadal następuje ruch szukający rozwarcia oraz świeże
  potwierdzenie otwartego wejścia na postoju. Zerokrokowe rozwarcie
  jest dopuszczone dopiero po poprawnym, dodatnim dojeździe do styku,
  tak jak przy zapamiętanych zboczach homingu Z.
- Kontakt bez wykonanego kroku, brak latch albo brak poprawnego
  rozwarcia nie daje zaakceptowanej próbki Z.
- Zachowane są dwie próbki, zgodność Z do 0,05 mm, powrót T0,
  limit prób i odzyskiwanie rozwarcia do 16 dodatkowych kroków w dół.
- Diagnostyka dodaje `touch_hit`, `touch_sent_steps`, `touch_raw`
  i `touch_qualification`. Brak kwalifikacji styku ma osobny komunikat
  `PC14 contact was not confirmed at pad center`.

Nie zwiększono limitów Z. Odjazd nadal wynosi `Z_CLEARANCE_MM=1`,
pomiar odbywa się przy 100°C w paśmie 90–110°C, nastawy mogą wynosić
do 310°C, heartbeat MCU ma timeout 5 s, a polityka drzwi jest globalna.

## Weryfikacja

- Odtworzenie przypadku z logu: T1, druga próbka Z, styk na wysokości
  110507 kroków, późniejszy odczyt otwarty. Kod R20 kończy cztery próby
  tym samym błędem. Kod R21 przechodzi pełne T0 → T1 → T0.
- Test zerokrokowego zwarcia potwierdza odrzucenie po czterech
  ograniczonych próbach, bez przejścia do T1.
- 541 testów hosta obejmuje również dotychczasowe przypadki trwałej
  niestabilności, przekroczeń temperatury, braku odpowiedzi, STOP,
  kwalifikacji XY, zapisu wyników i instalatora.
- Test rzeczywistego czytania konfiguracji przez przypięty Klipper.
- MCU R12 zachowuje SHA256:
  `5c890be7cccac852df31cab32b2db94d544ba34464377d61c34c798743828067`.

Pełny fizyczny przebieg R21 nadal wymaga potwierdzenia. Wyniki z tego
logu są częściowe i nie powinny być zapisywane jako kompletna kalibracja.

## Aktualizacja i próba

SSH na Orange Pi:

```bash
git -C "$HOME/inventure-orange-pi" pull --ff-only origin orange-pi-all-axis &&
python3 "$HOME/inventure-orange-pi/install-orange-pi.py" --configure-nozzles
```

MCU R12 pozostaw. Po instalacji `INVENTURE_VERSION` powinno pokazać
host `...PAD-XYZ-R21` i MCU `...PAD-THERMAL-R12`.

Przy pustych dyszach powtórz dotychczasowy przebieg:

```gcode
INVENTURE_CONTROL MODE=SERVICE
ZORTRAX_NOZZLE_PAD_CALIBRATE ACTION=START MODE=FULL SCAN=SWEEP PREP=EMPTY ACK=PAD_GEOMETRY_MEASURED T0_PAD_X=81.70 T0_PAD_Y=68.20 T1_PAD_X=70.80 T1_PAD_Y=68.70 T0_Z_MAX=139.50 T1_Z_MAX=137.95 Z_CLEARANCE_MM=1 PROCEDURE_TIMEOUT=3600
```

Odczyt:

```gcode
ZORTRAX_NOZZLE_PAD_CALIBRATE ACTION=STATUS
```

Oczekiwany wynik końcowy to `candidate`, obejmujący `T0`, `T1`
i `T0_RETURN`. Dopiero kompletny wynik można ocenić i zatwierdzić
osobnym poleceniem `SAVE`.
