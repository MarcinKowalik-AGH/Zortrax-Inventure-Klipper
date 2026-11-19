# Inventure — host R23: powtarzalne pionowe dotknięcia pada

R23 poprawia kwalifikację styków XY zarówno w pionowym sprawdzaniu po
SWEEP, jak i w samodzielnym SCAN=TAP. Aplikacja MCU R13 pozostaje identyczna.

## Przyczyna zatrzymania

Log `klippy(14).log(2).zip`, próba `c70982618fa54ea1be7661c8ef7a2aa3`:
T0 zakończyła XYZ. T1 przeszła pierwsze XY, a podczas ponownego pomiaru
X− host zakończył szukanie z przedziałem 66,8828125–68,5890625 mm.
Temperatury wynosiły T0 102,022°C i T1 100,894°C; status płyty był świeży,
sesja SERVICE aktywna, bez fault i latch. Nie wystąpił timeout ruchu.

MCU zapamiętywało styki PC14, lecz późniejsze odczyty wskazywały rozwarcie.
Przykładowe dojazdy: run 639 — 671 kroków, run 644 — 660 kroków,
run 669 — 661 kroków. Każdy miał latch PC14 i późniejszy odczyt otwarty.
R22 wymagał utrzymania zwarcia przez późniejsze sprawdzenie 150 ms i
odrzucał taki punkt. Po sześciu nierozstrzygniętych punktach w aktualnym
przedziale kończył pomiar. Pozostała niepewność wynosiła 1,70625 mm.

## Zmiana

Pionowy TAP używa istniejącej obsługi lokalnie zapamiętanego zbocza,
tak jak środkowy pomiar Z, z dodatkową kontrolą powtarzalności:

1. Dojazd Z musi wykonać dodatnią liczbę kroków i zatrzymać się na PC14.
2. Stół wraca do zweryfikowanej płaszczyzny odjazdu; odczyt końcowy musi
   wskazywać rozwarcie. Po co najmniej 25 ms następuje świeże sprawdzenie.
3. Ten sam punkt jest mierzony ponownie. Dwa dodatnie styki o różnicy
   wysokości najwyżej 40 kroków Z (około 0,04988 mm) potwierdzają styk.
4. Dwa pełne dojazdy do limitu bez zdarzenia styku potwierdzają punkt poza
   padem. Mieszane wyniki, zerokrokowe i niepowtarzalne styki pozostają
   nierozstrzygnięte i nie zmieniają potwierdzonych granic krawędzi.

Nie jest już wymagane utrzymywanie zwarcia po zatrzymaniu pionowego
dojazdu. Ślizgowy SWEEP nadal wymaga aktualnie zwartego styku, ponieważ
podczas tego ruchu następuje przesuwanie XY na wysokości kontaktu.
Końcowe pomiary Z oraz ich odzyskiwanie rozwarcia pozostają bez zmian.
Log dodaje dla pionowego punktu wynik, wysokości obu styków i liczbę
pełnych dojazdów bez styku.

Pozostają niezależne limity Z, sprawdzenia tacki, temperatur, czujników,
łączności, właściciela sesji i globalnej polityki drzwi. Pomiar odbywa się
przy 100°C ±10°C; odjazd domyślnie wynosi 1 mm, limit grzania 310°C,
heartbeat 5 s. Publiczny STOP nadal zatrzymuje całość.

## Tolerancja 0,1 mm

Do następnej próby podano `EDGE_TOLERANCE_MM=0.1` zgodnie z propozycją
użytkownika. Ten parametr ogranicza szerokość końcowego przedziału
krawędzi, nie jest stałym krokiem przejazdu. Wartość 0,05 mm pozostaje
dostępna i jest domyślna dla SWEEP. Powtarzalność wysokości dwóch
pionowych styków pozostaje ograniczona do około 0,05 mm w obu wariantach.

Samo zwiększenie tolerancji nie naprawiało błędu: negatywna kontrola
odtwarza zatrzymanie R22 na X− T1 przy obu wartościach. Nowa kwalifikacja
przechodzi cały pomiar T0 → T1 → T0 przy 0,05 i 0,1 mm.

## Weryfikacja

- 551 testów hosta, w tym pełny przebieg z otwarciem wejścia po
  zapamiętanych stykach T1 podczas powtórnego X−, przy obu tolerancjach.
- Dodatkowo pełny model T0 → T1 → T0 w SCAN=TAP oraz SWEEP z TAP.
- Odrzucenie mieszanych hit/miss, zerokrokowych styków i różnicy wysokości
  41 kroków; akceptacja 40 kroków. Ponowne zwarcie na odjeździe zatrzymuje
  próbę przed kolejnym dojazdem.
- 8 programów rejestrów MCU i rzeczywiste uruchomienie konfiguracji Klippera.
- Instalator weryfikuje obie zmienione biblioteki i przyjmuje poprzednie
  sumy R22. Obraz MCU R13 zachowuje SHA256:
  `7bb8a9334d2aade7eb38f4d949d73f2e10870cde4b12e3c8803ba7cdff0b8f35`.

Każdy punkt pionowy jest mierzony dwukrotnie. Testy pełnego TAP
uwzględniają ten dodatkowy czas. Próba fizyczna poniżej ma dotychczasowy
limit 3600 s; limity MCU nie są zwiększane. Pełny fizyczny wynik R23
pozostaje do potwierdzenia na drukarce. Kandydat nadal wymaga oceny przed
SAVE i nie jest automatycznie stosowany jako natywny offset podczas druku.

## Aktualizacja hosta

MCU R13 pozostaw. W SSH na Orange Pi:

```bash
git -C "$HOME/inventure-orange-pi" pull --ff-only origin orange-pi-all-axis &&
python3 "$HOME/inventure-orange-pi/install-orange-pi.py" --configure-nozzles
```

Po ponownym połączeniu `INVENTURE_VERSION` powinno pokazać host
`RC8-CONTROL-R7-UIFAST-PAD-XYZ-R23` i MCU `RC8-CONTROL-R7-PAD-THERMAL-R13`.
Jeśli wgrano starsze MCU, paczka R13 znajduje się również w tym wydaniu.

## Pełna próba przy pustych dyszach

```gcode
INVENTURE_CONTROL MODE=SERVICE
ZORTRAX_NOZZLE_PAD_CALIBRATE ACTION=START MODE=FULL SCAN=SWEEP PREP=EMPTY ACK=PAD_GEOMETRY_MEASURED T0_PAD_X=81.70 T0_PAD_Y=68.20 T1_PAD_X=70.80 T1_PAD_Y=68.70 T0_Z_MAX=139.50 T1_Z_MAX=137.95 Z_CLEARANCE_MM=1 EDGE_TOLERANCE_MM=0.1 PROCEDURE_TIMEOUT=3600
```

Po zakończeniu:

```gcode
ZORTRAX_NOZZLE_PAD_CALIBRATE ACTION=STATUS
```

Oczekiwany kompletny wynik to `candidate` z pomiarami `T0`, `T1` i
`T0_RETURN`. Do porównania z dokładniejszym przedziałem zmień tylko
`EDGE_TOLERANCE_MM=0.05`, zachowując pozostałe parametry.
