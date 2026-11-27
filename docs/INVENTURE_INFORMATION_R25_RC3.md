# Inventure R25 RC3 — naprawa startu Info i szybsza walidacja hosta

| Element | Wersja |
| --- | --- |
| Instalator | R25.3 |
| Info | `RC8-INFORMATION-R25.2` |
| Rdzeń hosta | `RC8-CONTROL-R7-HOMING-R38.6` |
| MCU | istniejące `RC8-CONTROL-R7-HOME-R25-INFO-OLED`, identyczne z RC1/RC2 |

**Nie wgrywaj ponownie MCU R25.** Poprawka dotyczy hosta; cztery wykonawcy
Home pozostają identyczne z zatwierdzonym profilem R24.

## Potwierdzona przyczyna wycofania instalacji

Dostarczony log zawiera dwukrotnie ten sam wyjątek z timera Info:

```
inventure_information.py, synchronize -> blocker
heaters.lookup_heater(name)
configparser.Error: Unknown heater 'heater_generic inventure_t1'
```

Rejestr Klippera zwraca z `get_all_heaters()` pełne nazwy sekcji, a
`setup_heater()`/`lookup_heater()` używa końcowego członu nazwy. Info przekazywało
pełną sekcję dla T1 i komór. Wyjątek następował po udanym odczycie i zapisie
fabrycznej tożsamości; instalator wycofywał host i konfigurację. Późniejszy
`Unknown command: INVENTURE_INFO` był skutkiem powrotu do starszej instalacji.

RC3 rozwiązuje nazwy tak samo jak `setup_heater()`: `inventure_t1`,
`chamber_l`, `chamber_r`. Odczytuje wszystkie cztery kanały oraz ich zadane
temperatury, moc i oczekujące PWM przed dopuszczeniem zapisu licznika. Nie
zmienia sterowania grzałkami ani ich ustawień.

Nowy test wykonuje synchronizator Info z rzeczywistym rejestrem grzałek
wczytanym przez zainstalowany Klipper oraz izolowanym reaktorem i wykonawcą
I/O. Odtwarza dokładny wyjątek poprzedniej wersji, potwierdza poprawny start
nowej i osiem odmów dla zadanej temperatury/PWM czterech grzałek. Wcześniejsze
testy parsera i wyświetlania nie wykonywały tego kroku startowego; tę lukę
uzupełniono. Fizyczny transport MCU jest w tym teście symulowany.

Końcowa kontrola instalatora sprawdza teraz również moduł i wersję Info,
aktywną sekcję konfiguracji, zakończenie pierwszej kontroli startowej i
rejestrację `INVENTURE_INFO`. Samo Ready z bazową wersją R38.6 nie wystarcza.

## Wynik Home Z z dostarczonego logu

Obie ukończone próby mają:

| Pole | Próba 1 | Próba 2 |
| --- | ---: | ---: |
| `cycles` | 102 | 102 |
| `non_improvements` | 101 | 101 |
| `completion_basis` | `stable_repetitions` | `stable_repetitions` |
| `terminal` | `done` | `done` |
| `release_steps_from_bottom` | 111691 | 111692 |
| `handoff_clearance_mm` | 1 | 1 |

Log potwierdza 102 pełne cykle kontakt–rozwarcie w każdej próbie. Obserwacja
pojedynczego widocznego dotknięcia obejmowała drobne ruchy pętli PC14; nie
oznaczała zakończenia po jednej próbce. Obie próby ustanowiły referencję.
Różnica zapisanych rozwarć wynosi jeden krok. To wynik dwóch dostarczonych
prób, a nie ogólna deklaracja dokładności mechanicznej.

Zachowano algorytm Inventure. Nie zastąpiono go pięcioma odjazdami M300.
W tej poprawce nie zmienia się ani jeden bajt `inventure_operator_home.py`,
`inventure_native_z.py`, `inventure_mcu.py` lub `inventure_z_home.py`.

## Czas przygotowania instalacji

Z katalogu raportu i czasu następnego startu w logu wynika około 305 sekund
przygotowania RC2 przed startem nowego hosta. Nie ma pomiaru poszczególnych
starych etapów. Zidentyfikowano jednak zbędną ponowną kompilację `c_helper.so`:
izolowany runtime dostawał świeże czasy modyfikacji źródeł C, więc parser
kompilował tę samą bibliotekę przy aktualizacji plików Pythona.

RC3 może użyć biblioteki z tego samego hosta wyłącznie po potwierdzeniu
identycznych manifestów wszystkich wejść C/H oraz definicji budowania i
ponownym sprawdzeniu ich zawartości. Kopia trafia tylko do izolowanego testu;
działająca biblioteka nie jest zmieniana. Przy braku zgodnego cache następuje
zwykłe budowanie. Test pełnej aktualizacji sprawdza rzeczywistą bibliotekę i
ponowne użycie bez różnicy jej bajtów. Osobny test odmawia użycia przy zmianie
źródeł. Czasy etapów są wypisywane i zachowane w `installation-timings.json`.

Pozostają pełna walidacja konfiguracji, kontrole MCU i transakcyjne wycofanie.
Kopie nadal obejmują tylko zmieniane pliki i ustawienia, jak w RC2. Nie
usuwamy wcześniej zachowanych kopii ani danych tożsamości i liczników.

## Instalacja na istniejącym MCU R25

Przy bezczynnej drukarce i wyłączonych celach grzania wykonaj we **Fluidd**
raz `FIRMWARE_RESTART`. Po Ready, przed Home i grzaniem, wykonaj w
**terminalu SSH Orange Pi**:

```bash
cd ~/inventure-src &&
git fetch origin --tags &&
git switch --detach inventure-information-r25-rc3 &&
python3 install-new-host.py --repair-home-procedures --serial /dev/ttyS5 --baud 750000 --apply
```

Po pomyślnym zakończeniu odczytaj we Fluidd `INVENTURE_INFO`.
Oczekiwany komponent to `RC8-INFORMATION-R25.2`. Powtórne bazowanie nie jest
potrzebne do sprawdzenia Info. Stare RC1/RC2 są zachowane jako uwierzytelnione
źródła aktualizacji; dane tożsamości i liczniki nie są zerowane.

Nowy start na fizycznej drukarce po tej poprawce pozostaje do potwierdzenia.
Testy oprogramowania, wcześniejszy udany odczyt OTP i dwa fizyczne pomiary Z
są odrębnymi obserwacjami.
