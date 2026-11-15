# Zortrax Inventure — kalibracja położenia dysz w XY

W menu OLED wybierz **Zortrax Inventure → Maintenance → Nozzle alignment**.
Tę samą procedurę obsługuje makro `ZORTRAX_NOZZLE_XY_TEST` w Fluidd.
Makro przekazuje argumenty do `INVENTURE_NOZZLE_XY_TEST`.

Kreator drukuje dwa grzebienie na tacce. T0 drukuje linie bazowe,
a T1 nakłada na nie linie w drugim kolorze lub z materiału podporowego.
Po lewej stronie wybiera się parę dla Y, a po prawej parę dla X.
Kryterium to najlepsze pokrycie obu linii na ich długości.
Przed kalibracją wypoziomuj stół i załóż czystą, prawidłowo osadzoną
tackę. Załaduj oba materiały i dobierz do nich temperatury.
[Instrukcja Zortraxa, strony 33–36](https://support.zortrax.com/wp-content/uploads/2022/01/user_guide_zortrax_inventure_2021.03.12.pdf#page=33).

## OLED

Potwierdź czystą i wypoziomowaną tackę, załadowanie obu materiałów oraz
sprawdzoną wysokość obu dysz. Po starcie ekran pokazuje etap przygotowania,
pomiaru i wydruku. Po ostygnięciu platformy i tacki obejrzyj wzorzec.
Wybierz linię w **Choose Left Y**, potem w **Choose Right X**, w zakresie
od −6 do +6. Środek obu grzebieni ma wartość 0.
**Review / save XY** wyświetla kandydata przed osobnym
potwierdzeniem zapisu. Do obu wyborów można wrócić z ekranu przeglądu.
Kreator OLED pokazuje także sesję uruchomioną z Fluidd.

## Fluidd

Skopiuj `config/INVENTURE_NOZZLE_XY.cfg` do katalogu konfiguracji drukarki
i dodaj `[include INVENTURE_NOZZLE_XY.cfg]` w `printer.cfg`. Możesz też
używać `INVENTURE_NOZZLE_XY_TEST` bez dołączania makra.
W `INVENTURE_VERSION` sprawdź, czy MCU zgłasza
`RC8-CONTROL-R7-NOZZLE-XY` i `nozzle_xy_abi: 1`.

Uruchom wydruk wzorców przy bezczynnej drukarce. Podane temperatury są
przykładem dla Z-PLA i Z-SUPPORT.

```gcode
INVENTURE_CONTROL MODE=SERVICE
ZORTRAX_NOZZLE_XY_TEST ACTION=START ACK=XY_PATTERN_SURFACE_READY T0_TARGET=210 T1_TARGET=215
ZORTRAX_NOZZLE_XY_TEST ACTION=STATUS
```

Gdy `STATUS` pokaże `phase: select`, odczytaj identyfikator sesji z pola
`run_id`. Poczekaj, aż platforma i tacka ostygną przed wyjęciem tacki do
odczytu. Wybierz najpierw pozycję z lewego grzebienia (Y), później z
prawego (X). Linie biegną od −6 po stronie mniejszej współrzędnej do +6 po
stronie większej. Linia 0 leży w środku. W przykładzie wybrano Y−1 i X+1.
Zastąp `<run_id>` wartością z własnego raportu.

```gcode
ZORTRAX_NOZZLE_XY_TEST ACTION=LINE AXIS=Y LINE=-1 RUN_ID=<run_id>
ZORTRAX_NOZZLE_XY_TEST ACTION=LINE AXIS=X LINE=+1 RUN_ID=<run_id>
ZORTRAX_NOZZLE_XY_TEST ACTION=STATUS
```

Po sprawdzeniu kandydata zatwierdź zapis tej samej sesji.

```gcode
ZORTRAX_NOZZLE_XY_TEST ACTION=APPLY ACK=XY_PATTERN_READ RUN_ID=<run_id>
```

`ACTION=STOP RUN_ID=<run_id>` przerywa wskazaną sesję. Jeśli najlepsza para
ma wartość −6 albo +6, zatwierdź jej poprawkę, oczyść tackę i powtórz wydruk.
Brzeg wzorca nie rozstrzyga, czy większa poprawka wyrówna dysze dokładniej.

## Przebieg ruchu

Kreator wykorzystuje wspólne bazowanie, mechaniczne przełączenie dysz i
pomiar rozwarcia PC14 na padzie. Dwie próbki T0 i trzy T1 ustalają lokalny
poziom Z dla drukowania każdego wzorca. Każda dysza drukuje 28 odcinków,
czyli po 13 linii i jednej poprzeczce na grzebień. Po ostatnim odcinku T0
stół opuszcza się o 10 mm przed zmianą dyszy. Po ostatnim odcinku T1 stół
opuszcza się o kolejne 10 mm względem wysokości wydruku T1. Głowica
parkuje nad koszem z wybraną T1, po czym obie grzałki są wyłączane.
Wyniki pomiaru Z służą temu wydrukowi. Zatwierdzanie XY zapisuje
wyłącznie poprawki XY.

## Nastawy

Domyślne temperatury OLED i Fluidd można zmienić opcjami
`nozzle_xy_t0_target` i `nozzle_xy_t1_target` w `[inventure_mcu]`.
Parametry `T0_TARGET` i `T1_TARGET` ustawiają temperaturę pojedynczej
sesji uruchamianej z Fluidd.

| Parametr | Domyślnie | Znaczenie |
|---|---:|---|
| `T0_TARGET`, `T1_TARGET` | 210 / 215°C | Cele oddzielnych regulatorów, 200–230°C |
| `MAX_POWER` | 40% | Limit mocy każdej z grzałek |
| `X_MODE` | `DIFFERENTIAL_X` | Stopniowana pozycja T1 w X. W `STOCK_REFERENCE` wzorzec X dopuszcza wybór neutralnej linii 0 |
| `XY_STEP_MM` | 0,05 mm | Przyrost poprawki między wariantami, 0,025–0,15 mm |
| `PATTERN_SHIFT_X`, `PATTERN_SHIFT_Y` | 0 mm | Przesunięcie obu grzebieni sprawdzane dla całej trajektorii |
| `T0_PROFILE` | `STANDARD` | Profil T0 lub `MATERIAL14` z fabrycznej ścieżki ID 14 |
| `T0_GAP_MM`, `T1_GAP_MM` | 0,8 / 1,1 mm | Odległości drukowania od zmierzonego poziomu dyszy |
| `PATTERN_LIFT_MM` | 0,2 mm | Dodatkowy prześwit na przejazd do początku linii |
| `PATTERN_TRAVEL_SPEED`, `PATTERN_Z_SPEED`, `PATTERN_E_SPEED` | 30 / 2 / 10 mm/s | Prędkość przejazdu, Z i ograniczenie prędkości retrakcji |
| `T0_FLOW`, `T1_FLOW` | 100% | Mnożniki podania materiału dla linii |
| `Z_MAX_SPREAD_MM` | 0,15 mm | Dopuszczalny rozrzut punktów odniesienia danej dyszy |

Limit 960 000 kroków E obejmuje łączny ruch ekstrudera w sesji.
Przy domyślnych nastawach plan `STANDARD` obejmuje 714 240 kroków,
a `MATERIAL14` 851 040. Plan przekraczający limit jest odrzucany
podczas sprawdzania nastaw.

Kalibracja wyznacza poprawki XY położenia T1 względem T0.
Argument `LINE` podaje pozycję względem środka bezpośrednio, od −6 do +6.
Korekta jest przyrostowa według wzoru `nowy offset = poprzedni offset − LINE × krok`.
Standardowe 0,05 mm daje do ±0,30 mm na przebieg w każdej osi.
Domyślny geometryczny odstęp dysz w X wynosi 10,8 mm. Zapisane X/Y są
poprawkami względem tej geometrii. W układzie serwisowym dodanie
dodatniej poprawki do logicznego położenia dyszy oznacza odsunięcie
karetki w ujemnym kierunku na tej osi.

`APPLY` atomowo zapisuje wynik w `inventure_nozzle_xy.json` obok
`printer.cfg`. `STATUS` pokazuje ostatnio zapisane poprawki w polu
`stored_residual_mm`. Kolejny wzorzec używa tych poprawek.
Późniejsze użycie tego pliku podczas zwykłego druku wymaga integracji
z natywną zmianą narzędzia. Osobna próba
[wysokości Z dysz](NOZZLE_Z_TEST_R0323.md) podaje wynik w osi Z.

## Weryfikacja z firmware 1.6.1 r0323

| Etap | Ustalenie z aplikacji OEM | Adres |
|---|---|---|
| Start | Kreator sprawdza tackę i kosz, odczytuje dostępne dane materiałów | `0x08018584–0x08018638` |
| Temperatura przy przygotowaniu | T0 200°C i T1 160°C. T1 zaczyna dogrzewanie do 200°C jeszcze podczas drukowania T0 | `0x08018654–0x08018660`, `0x080187D8–0x080187DC` |
| Temperatura wzorca | Faktyczne nastawy zależą od katalogu materiałów. Brak rozpoznanej pozycji zwraca 230°C. Przykłady z katalogu to Z-PLA 210°C, Z-PLA Pro 200°C, Z-SUPPORT 215°C i Z-SUPPORT Premium 220°C | `0x08014B18`, `0x08014B50`, `0x08018796`, `0x080188DE`, `0x08029DB8` |
| Lewy wzorzec Y | Poprzeczka od (10; 95,5) do (10; 35,5). Linie X od 10 do 55 mm dla Y równych 65,5 + 5 × indeks. W T1 firmware dodaje do Y także 0,05 × indeks | `0x08018390–0x0801841F` |
| Prawy wzorzec X | Poprzeczka od (132,5; 43) do (72,5; 43). Linie Y od 43 do 88 mm dla X równych 102,5 + 5 × indeks. Oba narzędzia wywołują ten sam generator z tą samą siatką XY | `0x08018420–0x0801848B`, wywołania `0x080187E6` i `0x08018920` |
| Ruch jednej linii | Uniesienie o 0,2 mm, przejazd, opuszczenie, podanie E, druk, retrakcja i 2 mm wyjazdu w kierunku linii. Rekordy profili dotyczą kolejno T0 standard, T0 materiał 14 i T1 | `0x08017F40`, `0x08036BA8`, `0x08036BC4`, `0x08036BE0` |
| Ocena | Wewnętrzny wybór od −6 do +6, na fabrycznym ekranie 1–13 ze środkiem 7. Najpierw Y i rekord 8, następnie X i rekord 7. Nowa poprawka to stara minus wybór × 0,05 mm | `0x0801821C`, `0x080182D8`, `0x08018A4E–0x08018A6E`, `0x08018AA0–0x08018ABE` |

W trybie `DIFFERENTIAL_X` Klipper dodaje w prawym grzebieniu
stopniowany przyrost X dla T1, analogicznie do fabrycznego grzebienia Y.
Dzięki temu wybór prawej pary wynika z widocznej zmiany pokrycia.
OLED i Fluidd pokazują pozycje −6 do +6 z neutralnym 0 w środku.
W pozostałych wymiarach wzór zachowuje fabryczne położenia, długości i
rozstaw. Zakres temperatur ogranicza się do 200–230°C. Fabryczny profil
ruchu `MATERIAL14` dotyczy Z-SEMIFLEX, którego katalogowa temperatura
235°C wykracza poza ten zakres.

Podstawą jest `application_r0323.bin` (SHA-256
`9b980ba04531fac4425b77a23969076d24d8f25629fc2a501ac05d262c142268`)
i listing Thumb z archiwum projektu
`ZORTRAX_INVENTURE_KOMPLET_2026-10-02_CZESC_1_z_2.zip`. Archiwum źródeł
RC7.2 zawiera test `test_alignment_binary_oracle.py`, który porównuje
oba oryginalne generatory, ruch E i końce odcinków z odtworzoną geometrią.
