# Inventure — punkt 5: pełne cykle R22

Stan: **testy programowe; kwalifikacja fizyczna nadal niewykonana**.
Baza: R22, commit `2c40ce0d95d8a007a0533cfdf371db84195c2cf7`, PR #7.

## Co sprawdza nowy test

`ci/check_inventure_full_cycles.py` przygotowuje host tym samym bootstrapem
co instalator, składa rzeczywiste pliki konfiguracji i uruchamia parser
przypiętego Klippera. Polecenia Fluidd przechodzą przez zainstalowane makra,
Jinja i parser parametrów; wybory OLED przez zainstalowany serwis menu.
Wykonywane są zainstalowany adapter Home, interpreter fabryczny R22,
weryfikacja `owner/run/token` i odczyt zwrotny NATIVE.

Model zastępuje fizyczne interfejsy: UART/odpowiedzi MCU, kolejkę i pozycję
napędów, krańcówki, lokalne dotknięcia Z, grzałki i lease kontrolera
operacji. Test nie wykonuje prawdziwej generacji STEP w rdzeniu Klippera,
nie mierzy obciążenia Orange Pi ani czasów IRQ STM32 i nie otwiera UART.
Osobny test C wykonuje rzeczywisty kod MCU na modelu rejestrów i zegara.
Wyniki tych dwóch warstw nie są dowodem fizycznego wydruku ani usunięcia
`Timer too close`.

### Host z zainstalowanych plików

Każda sesja wykonuje bez restartu trzy powtórzenia:

`Home X → Home Y → Home XY → Home All → Home Z → Home XY`.

Są dwie sesje: start w zimnym OFF i start w NATIVE z aktywnymi celami
T0/T1 oraz komory. Łącznie: **36 poleceń Home i 12 kompletnych przejęć
SERVICE → NATIVE**. Powtórzenia obejmują polecenia Fluidd i wybory OLED.
Home Z/All wymagają potwierdzenia i wykonują wybór/czyszczenie T0.

Sprawdzane skutki:

- zwykłe X/Y/XY nie wchodzą do interpretera SERVICE;
- tylko Klipper ustanawia XY; SERVICE nie wykonuje drugiego Home XY;
- dla All kolejność natywnych bazowań to Z/Y/X/Z, a dla kolejnego Z — Z/Z;
- pełny wybór mechaniczny T0 poprzedza kontakt Z;
- przekazanie kończy się odczytanym i zweryfikowanym NATIVE;
- Z jest certyfikowane dopiero po pełnym pomiarze i końcowym odjeździe;
- cele wszystkich czterech natywnych grzałek są zachowane po sukcesie;
- nie pozostaje zajęty właściciel Home ani aktywny kontekst SERVICE;
- pliki konfiguracji i zapisanej kalibracji nie zmieniają się.

Dziewięć oddzielnych scenariuszy sprawdza: brak tacki, druk, pauzę,
zatrzaśnięty błąd MCU, brak połączenia, anulowanie i ponowienie starego
potwierdzenia, błędny token przekazania, timeout kontaktowego ruchu Z po
wcześniejszym udanym Home oraz unieważnienie potwierdzenia przez shutdown.
Błędy po rozpoczęciu procedury muszą wyzerować cele grzałek, zakończyć
SERVICE i odmówić publikowania nowego Z=0. Odmowy przed startem nie mogą
uruchamiać napędów ani zasilania.

### Rzeczywisty kod MCU na rejestrach testowych

`service_mcu/control_r6/TESTS/test_r22_full_cycles.c` wykonuje:

- 12 kolejnych NATIVE → SERVICE → grzanie/ruch → NATIVE bez resetu;
- wyłączność STEP, utrzymanie tożsamości i blokad fizycznych;
- poprawny powrót pomimo zimnego PA4 bez próbki lub progu minimum
  ustawionego wyłącznie dla SERVICE;
- monitorowanie T0 rozgrzanej w SERVICE i zwolnienie wymagania po
  potwierdzonym schłodzeniu;
- ponowne wykorzystanie tych samych uchwytów PWM grzałek;
- 500 sekund symulowanego czasu, 13 przepełnień zegara i spóźnione
  wywołania próbnika, z modelowanymi zboczami tachometrów;
- zadziałanie PC13 po długiej sesji, OFF oraz odmowę ponownego uzbrojenia
  starym potwierdzeniem przekazania.

Program wchodzi do standardowego `run_mcu_tests.py`. Model nie uruchamia
rzeczywistego rdzenia harmonogramu Klippera, DMA OLED ani elektrycznego UART.

## Uruchomienie programowe

W środowisku testowym z zależnościami z `.github/workflows/orange-pi-host.yml`:

```bash
python3 -m unittest discover -s tests -v
python3 service_mcu/control_r6/TESTS/run_mcu_tests.py
python3 ci/check_inventure_full_cycles.py --output full-cycles.json
```

`--repetitions 3..100` zmienia długość serii. Raport JSON zawiera
`hardware_tested=false`, liczbę cykli, odmowy oraz SHA256 rzeczywiście
załadowanych modułów. Workflow hosta zachowuje go jako artefakt
`INVENTURE_R22_FULL_CYCLES_SIMULATED`.

Dotychczasowy zestaw regresji dodatkowo pokrywa pełne procedury T0/T1
(SELECT/CLEAN/PURGE/LOAD/UNLOAD), poziomowanie OEM, interaktywne potwierdzenia,
OLED, konfigurację, instalację i rollback. Nowa seria skupia się na
połączeniu tych warstw przy Home i powtarzaniu przejęć, a nie na zastąpieniu
wszystkich istniejących testów jednym modelem.

## Zgodność i pliki produkcyjne

Wymagany obraz: `RC8-CONTROL-R7-HOMING-R22`, z ABI
`INVENTURE_SERVICE_NATIVE_SPLIT_ABI=1`, `INVENTURE_POLICY_STATUS_ABI=1`
i harmonogramem R21. Przygotowany wcześniej `INVENTUR.IVB` R22 ma SHA256:

`0511bd906fa6260c2ecb6b7b89f0ce35727759884179ecd6411b1e15e08f99f0`.

Punkt 5 dodaje testy, raportowanie i dokumentację. Nie zmienia źródeł
produkcyjnego MCU, bootloadera, bibliotek hosta, geometrii ani prędkości.
Nie wymaga budowania nowego obrazu ani nadawania wersji R23.

## Próby na fizycznej Inventure — pozostałe kryteria

Poniższe czynności nie zostały wykonane przez CI ani przez lokalny model.
Wykonuje je operator na zgodnym hoście i MCU R22. Nie należy uznawać
samego uruchomienia usługi albo jednego Home All za zaliczenie.

1. Zapisać wersje i stany przed próbami:

   ```gcode
   INVENTURE_VERSION
   INVENTURE_SAFETY_POLICY_STATUS
   INVENTURE_TIMING_STATUS
   INVENTURE_HOME_Z_STATUS
   ```

2. Wykonać kolejno Home X, Y, XY, All, Z i ponownie XY. Każde polecenie
   wysłać oddzielnie i zaczekać na zakończenie oraz wymagane potwierdzenie.
   Powtórzyć całą serię co najmniej trzykrotnie bez restartu. Sprawdzić
   także te same akcje z fizycznego OLED.

3. Powtórzyć serię przy już rozgrzanej drukarce, używając sprawdzonych
   temperatur roboczych. Zweryfikować fizyczny wybór T0, czyszczenie,
   prawidłowy kierunek Z i odjazd oraz zachowanie wcześniejszych celów
   grzałek po poprawnym zakończeniu.

4. Zebrać końcowe stany polityki i harmonogramu oraz cały `klippy.log`.
   Sprawdzić potwierdzenia `INVENTURE_OWNER_AUDIT`, brak `Timer too close`,
   rzeczywistych tripów i utraty połączenia. Analizator:

   ```bash
   python3 tools/inventure_fault_analyzer.py ~/printer_data/logs/klippy.log
   ```

5. Osobno przejść fizyczny kreator poziomowania OEM oraz krótki wydruk
   na sprawdzonych parametrach, a następnie ponownie Home XY i All.
   Te próby sprawdzają zachowanie po poprzedniej procedurze i po druku.

**Mesh:** obecny odtworzony profil nie konfiguruje natywnego obiektu
`probe`. Dlatego nie można zaliczyć `BED_MESH_CALIBRATE` jako wykonanego
natywnego pomiaru. Pięciopunktowy kreator OEM jest odrębną procedurą.
Kwalifikacja natywnego mesha pozostaje otwarta wraz z konfiguracją sondy.

Do czasu tych obserwacji issue #3 pozostaje nierozstrzygnięte, a kandydat
R21/R22 nie otrzymuje oznaczenia stabilnego wydania produkcyjnego.
