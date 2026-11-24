# Inventure R22 — rozdzielenie SERVICE (rygorystyczny) i NATIVE (normalny)

## Cel

Przywrócić architekturę M300 w zakresie polityk: normalne HOME, G0/G1,
drukowanie i natywne poziomowanie są własnością Klippera, a dodatkowe
wymagania fabrycznych pomiarów obowiązują tylko w ich sesji SERVICE.
Poprawka NIE omija czujnika platformy PC13, dyszy PC14, kabla PD11,
heartbeat, kontroli grzałek, wentylatorów i ograniczeń absolutnych.

### Wykryty błąd R21

STM32 przy końcowym `factory_native_handoff` porównywał wszystkie
temperatury z `control_guard_mask|31`, w tym z modyfikowalnymi
`control_min/max` z kalibracji SERVICE. Niezwiązany z aktywną grzałką
czujnik PA4 albo podniesiony próg serwisowy T1 powodowały odmowę
przekazania napędów, nawet gdy normalny Klipper mógł bezpiecznie
wykonywać HOME Z/All. W R22 ostatnia kontrola stosuje
`native_sensors_valid(now)` zamiast `service_sensors_valid(...)`.

## Rozdzielenie polityk

| Zachowanie | SERVICE | NATIVE |
|---|---|---|
| Maska i zakresy `INVENTURE_GUARD` | Obowiązują; krytyczne kanały 0–4 nadal wymuszane | Nie są używane do oceny zwykłego ruchu |
| Zimny, niezasilany sensor niepowiązany z ruchem | Wymagany, jeśli w masce SERVICE | Brak przymusowego odcięcia tylko z powodu braku próbki |
| Wartość odczytu ponad bezwzględnym maksimum | Tak, w ramach serwisowej kontroli | Tak, jeśli odczyt wiarygodny |
| Czujnik aktywnej / niedawno gorącej grzałki | Obowiązkowy | Obowiązkowy, także po wyłączeniu PWM do potwierdzonego schłodzenia |
| PSU i płyta główna | Obowiązkowe przy ustawionym profilu SERVICE | Obowiązkowe podczas grzania |
| T0/T1 | Rygorystyczne okna kalibracji | Maksimum 310°C |
| Komora | Rygorystyczne okna i czujniki | Maksimum 150°C |
| PSU i elektronika | Rygorystyczne okna | Maksimum 100°C |
| PCB głowicy | Rygorystyczna kontrola | Maksimum 190°C, gdy odczyt ważny |
| PC13, PC14, PD11, heartbeat, wentylatory | Ochrona fizyczna niezmieniona | Ochrona fizyczna niezmieniona |
| Ruch i STEP | Wyłącznie wydzielona procedura serwisowa | Planer i kolejki Klippera |

### Istotna reguła termiczna

Jeżeli T0/T1 pracowała w SERVICE i grzałka została już wyłączona,
STM32 nie zapomina stanu gorącej dyszy. `native_hot_mask` jest
aktualizowana w czasie SERVICE, a uruchomienie serwisowej grzałki
od razu zapisuje gorący kanał. Powrót do NATIVE nadal odmawia ruchu,
jeśli czujnik takiej gorącej dyszy zniknie. Zapis znika dopiero po
**ważnym** pomiarze do 40°C i wyłączonym zasilaniu grzałki.
W ten sposób usunięcie błędnego przymusu odczytu zimnego PA4
nie osłabia ochrony rozgrzanej T0/T1.

## Diagnostyka

W host R22 dostępna jest komenda odczytowa:

```gcode
INVENTURE_SAFETY_POLICY_STATUS
```

Wynik rozróżnia `NORMAL`, `STRICT_SERVICE` oraz `NO_ACTIVE_OWNER`
i pokazuje aktualną maskę SERVICE, wymagane gorące/zasilane
czujniki NATIVE oraz limity absolutne. Odczyt nie przełącza
trybu, nie zasila grzałek i nie uruchamia silników. Sam MCU
udostępnia `inventure_policy_query`, także po awaryjnym
zatrzymaniu, o ile firmware nadal odpowiada na UART.

## Instalacja

R22 zachowuje poprzednią gałąź R21 jako punkt powrotu, pliki
`inventure_native_z.py`, `inventure_z_home.py`, cały OLED,
geometrię i istniejący weryfikowany mechanizm przekazywania
`owner/run/token`. Jedyną aktualizacją hosta względem R21 jest
`klippy/extras/inventure_mcu.py` oraz manifest — z kontrolą hash
i kopią umożliwiającą przywrócenie poprzednich bajtów.
Firmware wymaga `INVENTURE_SERVICE_NATIVE_SPLIT_ABI=1`
oraz `INVENTURE_POLICY_STATUS_ABI=1` w słowniku R22.

## Walidacja i ograniczenia

Testy:
- 13 wariantów zakończenia SERVICE: normalny powrót, odmowy
  właściciela, niepełnego ruchu, braku PE12/PC14 oraz
  rzeczywistego uszkodzenia gorącego T0 i elektroniki przy PWM;
- konfiguracja zakresu/minimum czujnika wyłącznie SERVICE
  nie może blokować zdrowego NATIVE;
- monitorowanie rozgrzanej dyszy przez SERVICE → NATIVE;
- fizyczna utrata PC13 nadal zatrzaskuje MCU w OFF;
- odczyt polityki nie wydaje żadnych komend ruchu/grzania;
- wszystkie regresje R19/R20 (Home i SERVICE handoff),
  R21 (10/20 kHz i `Timer too close`), OLED i instalacja;
- kompilacja STM32F207 GNU ARM 14.2.1, walidacja słownika
  firmware, obrazu `INVENTUR.IVB` i SHA256.

**Ważne:** testy programowe nie potwierdzają działania fizycznego.
R22 nie jest jeszcze produkcyjnym wydaniem i nie oznacza naprawy
całego `Timer too close`. Ostateczna akceptacja wymaga serii
powtarzalnych HOME X/Y/Z/All i testów temperatur oraz komunikacji
na rzeczywistej Inventure. Nie instalować w stanie grzania/ruchu
i nie podnosić parametrów prędkości bez takich prób.
