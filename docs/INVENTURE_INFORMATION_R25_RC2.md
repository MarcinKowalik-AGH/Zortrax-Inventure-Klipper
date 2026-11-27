# Inventure R25 RC2 — naprawa instalatora, cache Pythona i zakres kopii

Instalator R25.2 naprawia zgłoszone zatrzymanie na
`klippy/extras/__pycache__/inventure_home_ui.cpython-313.pyc`.
Funkcja Info pozostaje `RC8-INFORMATION-R25.1`. Aplikacja MCU jest identyczna
z R25 RC1: `RC8-CONTROL-R7-HOME-R25-INFO-OLED`; ponowne wgrywanie MCU R25
nie jest potrzebne.

## Cache a weryfikacja źródeł

Dotychczasowy walidator dopuszczał cache tylko wtedy, gdy odpowiadający mu
plik `.py` należał do sprawdzanego manifestu. Po próbie nowszego interfejsu
lub wycofaniu aktualizacji mógł zostać prawidłowo nazwany cache modułu,
którego starszy manifest już nie zawiera. Sam zgłoszony komunikat nie
identyfikuje, kiedy taki plik został utworzony na drukarce.

RC2 rozpoznaje format `__pycache__/module.cpython-NNN[.opt-1|.opt-2].pyc`
wewnątrz katalogów zawierających uwierzytelnione źródła Pythona. CPython nie
importuje takiego osieroconego cache bez odpowiadającego źródła `.py`.
Cache pozostaje na miejscu; nie kasujemy plików z działającego hosta.
Obce źródła, biblioteki, nietypowe pliki, dowiązania, zmiany sum i uprawnień
nadal powodują odmowę. Testy obejmują dokładną nazwę cache Python 3.13 ze
zgłoszenia, jego nieimportowalność bez źródła i odmowę po dodaniu obcego `.py`.

## Krótsze przygotowanie i kopie

- Rozpoznawanie starszego runtime zaczyna od RC7/RC6, zamiast za każdym razem
  odtwarzać wszystkie najstarsze wersje. Każdy wybrany manifest i plik nadal
  podlega pełnej weryfikacji.
- Usunięto dodatkowe kopiowanie całego katalogu konfiguracji do `before-config`.
  Ta druga kopia obejmowała również nieaktywne pliki i stare archiwa ZIP.
- Mechanizm transakcji nadal zachowuje każdy zmieniany plik, poprzedni plik
  usługi i ustawienia Fluidd oraz odtwarza je po błędzie. Niezmieniane pliki
  pozostają na miejscu. Nie usuwamy wcześniejszych kopii użytkownika.
- Konsola rozróżnia teraz weryfikację runtime, przygotowanie konfiguracji,
  walidację offline i właściwe kopiowanie zmienianych plików.

Punktem odniesienia jest aktywne `M300_HOST/install.py` M300: kopia zawiera
poprzednią zawartość zmienianych plików. Sprawdzenie całego hosta i konfiguracji
Inventure pozostaje potrzebne przed przełączeniem usługi.

## Home Z — wynik porównania, bez zmiany ruchu

M300 wykonuje **pięć kolejnych par kontakt–rozwarcie PC7**, a odniesienie
wyznacza z piątego rozwarcia. Potwierdza to aktywne
`M300_HOST/payload/m300_native_z.py`, klasa `M300ZRefinement`, `contacts = 5`,
blob `d33bbefd8e374b534b25cd15c7ba9998d26011d3`.

Inventure r0323 ma inny algorytm: `0x0801DDB0` wywołuje `0x0801DC70` z
licznikiem 100. Kod `0x0801DCB4–0x0801DCB8` zwiększa licznik braku poprawy
i kontynuuje przy `<= 100`, co daje **101 kolejnych cykli bez lepszego
rozwarcia**. Poprawa wyniku zeruje licznik. Pomiar zachowuje najlepsze
rozwarcie, zamiast wybierać piątą próbkę. Te drobne ruchy nie są pięcioma
pełnymi, widocznymi odjazdami stołu.

Istnieje też wcześniejsza ścieżka zakończenia po osiągnięciu górnej granicy
szukania z już zmierzonym rozwarciem. W naszym wyniku jest jawnie oznaczona
`completion_basis: upper_bound_with_best_release`; zwykłe zakończenie ma
`completion_basis: stable_repetitions`. Dlatego samej obserwacji jednego
dotknięcia nie można utożsamiać z ukończeniem 101 cykli. Trzeba odczytać
`INVENTURE_HOME_Z_STATUS` przed restartem i sprawdzić `last_result.cycles`,
`non_improvements`, `completion_basis` oraz `terminal`.

Źródłem porównania jest oryginalna aplikacja r0323, SHA-256
`9b980ba04531fac4425b77a23969076d24d8f25629fc2a501ac05d262c142268`, oraz
[wcześniejszy audyt PC14](Z_CONTACT_AND_HOMING_R0323.md#sekwencja-firmware-161-r0323).
Zgłoszony nowy przebieg fizyczny wymaga jeszcze odczytu; nie traktujemy
obserwacji pojedynczego dotknięcia jako potwierdzenia prawidłowego pomiaru.

RC2 nie zmienia żadnego wykonawcy Home ani MCU. Cztery pliki
`inventure_operator_home.py`, `inventure_native_z.py`, `inventure_mcu.py`,
`inventure_z_home.py` są identyczne z RC1 i zachowanym profilem R24.

## Aktualizacja hosta po wgraniu MCU R25

Najpierw zachowaj wynik statusu Z, jeżeli diagnozujesz poprzedni Home.
Przy bezczynnej drukarce i wyłączonych celach grzania wykonaj raz
`FIRMWARE_RESTART` w Fluidd. Poczekaj na Ready. Przed kolejnym Home lub
grzaniem uruchom na Orange Pi:

```bash
cd ~/inventure-src &&
git fetch origin --tags &&
git switch --detach inventure-information-r25-rc2 &&
python3 install-new-host.py --repair-home-procedures --serial /dev/ttyS5 --baud 750000 --apply
```

Odmowa przy uzbrojonym MCU pozostaje aktywna: zatrzymanie starego hosta
mogłoby przerwać wymagany heartbeat. `FIRMWARE_RESTART` przygotowuje do
przełączenia usługi; samo ponawianie instalatora nie rozbraja MCU.

Po Ready odczytaj `INVENTURE_INFO`. Oczekiwany komponent:
`RC8-INFORMATION-R25.1`. [Pełny opis funkcji Info](INVENTURE_INFORMATION_R25.md).

Nowe próby instalatora i regresje są programowe. Nie wykonano zdalnie
ruchu, grzania ani zapisu na fizycznej drukarce.
