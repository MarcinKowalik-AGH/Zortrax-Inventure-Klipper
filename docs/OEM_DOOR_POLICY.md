# Inventure — globalna opcja Door lock

Zachowanie fabryczne opisuje audyt
`Inventure_EXTERNAL_DRZWI_WENTYLATORY_TERMISTORY_2026-10-02/RAPORT_PL.md`,
rozdział 4, oraz `evidence/03_door_UI_and_wait.txt`.
Analizowany firmware 1.6.1/r0323, SHA256:
`9b980ba04531fac4425b77a23969076d24d8f25629fc2a501ac05d262c142268`.

| Element OEM | Potwierdzone zachowanie |
|---|---|
| Callback `0x080173E4` | Włącza politykę przez setter `0x0801168C(3, 0)` |
| Callback `0x0801740C` | Wyłącza politykę przez setter `0x0801168C(3, 1)` |
| Procedura `0x08014F74` | Sprawdza bit 3 przez `0x08011674(3)`. Ustawiony bit kończy procedurę przed kontrolą czujników i załączeniem zamków. |
| Ostrzeżenie `0x08036644` | Po wyłączeniu przypomina o ruchomych częściach |
| Klapa | Wejście PE15, zamknięta=1; maska 1; zamek PA3/TIM5 CH4 |
| Przednie drzwi | Wejście PB10, zamknięte=1; maska 2; zamek PA2/TIM5 CH3 |

Gdy polityka jest włączona, OEM stosuje przyciągnięcie CCR=256 przez
100 ticków i podtrzymanie CCR=60, a zwolnienie ma CCR=0. Audyt nie odtworzył
wszystkich reakcji całego programu na otwarcie podczas wydruku lub błędu.
Nie przypisujemy tej nieustalonej części zachowania do portu.

## Ustawienie CASSETTE-POLICY

`RC8-CONTROL-R7-CASSETTE-POLICY` uruchamia się z opcją Door lock
wyłączoną. Bit 3 w RAM MCU obejmuje wspólnie przednie drzwi i górną
klapę. Przy `ENABLE=0` MCU zwalnia cewki, anuluje timery zamków
i odcina kolejne próby załączenia ich wyjść. Sesja FACTORY uwzględnia
to ustawienie podczas AUTO i UNLOAD dla T0 i T1, także przy otwartych
osłonach. Raport pokazuje fizyczne
stany obu czujników. Nadzór krańcówek, grzania, łącza i STOP działa
niezależnie od bitu 3.

Odczyt w konsoli Klippera.

```gcode
INVENTURE_DOOR_POLICY
```

Domyślny stan tego wydania to `scope: global`, `enabled: false`,
`default_enabled: false` i `lock_command_mask: 0`.
`front_door_closed` oraz `lid_closed` są stanami czujników. Maska
opisuje polecenia wyjść zamków. Położenie mechanizmu zamka sprawdza się
na drukarce.

Zmianę wykonuje się w stanie bezczynnym. `INVENTURE_DOOR_POLICY ENABLE=0`
wyłącza kontrolę obu osłon i zwalnia załączone zamki.
`INVENTURE_DOOR_POLICY ENABLE=1` przywraca warunek zamknięcia osłon
i pozwala sterować zamkami w bieżącej sesji. Istniejąca opcja Door lock
na OLED wywołuje te same komendy. Restart tego MCU stosuje ponownie
domyślne `ENABLE=0`. Ustawienie znajduje się w RAM aplikacji.

`INVENTURE_VERSION` zgłasza `door_policy_global: true` i
`door_policy_default_enabled: false` dla CASSETTE-POLICY. Starsze
wydania z ABI 1 mają domyślne `ENABLE=1` po restarcie. Na nich
zmianę wykonuje się w bezczynnej drukarce po zwolnieniu zamków.

Testy rejestrów obejmują oba stany polityki, wyjścia zamków i sesje
FACTORY z otwartymi osłonami. Fizyczną próbę T0 i T1 opisuje
[instrukcja ładowania kaset](FACTORY_LOAD_2026-11-10.md#próby-z-prędkościami-oem).
