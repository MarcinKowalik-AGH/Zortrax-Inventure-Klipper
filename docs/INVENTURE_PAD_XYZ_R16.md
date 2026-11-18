# Zortrax Inventure R16 / MCU R10 — naprawa startu T1

Klipper nie uruchamiał się po dodaniu natywnej grzałki T1 przez instalator
R14/R15. Sekcja `[heater_generic inventure_t1]` trafiała na początek
`printer.cfg`, przed `[inventure_mcu]`. Klipper próbował więc utworzyć
czujnik T1, zanim moduł płytki zarejestrował typ `Inventure R5 ADC`.
Kończyło się to komunikatem `Unknown temperature sensor 'Inventure R5 ADC'`.

Konfiguracja z `klippy(12).log` z 18 listopada 2026 odtwarza dokładnie ten
błąd w dostarczonym, przypiętym kodzie Klippera. To błąd instalatora.
R16 naprawia również konfigurację już zmienioną przez poprzednie wydanie.

## Co zmienia R16

- Dodaje T1 po konfiguracji płytki i plikach `include`.
- Przenosi istniejącą sekcję T1, jeśli została umieszczona przed modułem płytki.
- Zachowuje wartości opcji, komentarze wewnątrz sekcji i ustawienia ruchu.
- Umieszcza aktywną konfigurację przed stopką `SAVE_CONFIG`.
- Sprawdza kolejność po rozwinięciu wszystkich plików `include`, przed
  zatrzymaniem usługi i zapisem plików.
- Zachowuje kopie konfiguracji i odwracanie aktualizacji przy błędzie zapisu
  lub uruchomienia usługi.

Limit obu natywnych grzałek pozostaje równy 310°C. Współdzielone chłodzenie
uwzględnia T0 i T1. MCU pozostaje R10, pomiar pada odbywa się przy 100°C,
a czas utraty komunikacji pozostaje równy 5 sekund. R16 nie zmienia
trajektorii ani algorytmu pomiaru.

## Co potwierdza przesłany log

| Zdarzenie | Ustalenie |
| --- | --- |
| Ostatnie udane połączenie MCU | `RC8-CONTROL-R7-PAD-THERMAL-R8`, limit serwisowy 290°C |
| Utrata komunikacji | Poprzedzona utratą potwierdzeń OLED; log nie rozstrzyga przyczyny |
| Kolejne uruchomienia Klippera | Pięć prób zatrzymanych przy tworzeniu czujnika T1 |
| Konfiguracja po aktualizacji | T1 przed modułem płytki, obie grzałki 310°C, chłodzenie powiązane z obiema |
| Kalibracja po tych restartach | Brak uruchomionej procedury; host zatrzymuje się podczas czytania konfiguracji |

Z tego pliku nie wynika, że aktualizacja MCU do R10 została wykonana.
Błąd rejestracji czujnika występuje przed ponownym połączeniem z MCU.

## Weryfikacja

- 525 testów hosta, w tym naprawa rzeczywistej konfiguracji z logu,
  zagnieżdżone `include`, zachowanie stopki `SAVE_CONFIG`, ponowna
  instalacja bez dalszych zmian i odmowa zapisu przy złej kolejności T0.
- Osobny test rzeczywistego `Printer._read_config()` z przypiętego Klippera.
  Kontrola ujemna odtwarza błąd R15. Obie kontrole dodatnie — naprawiona
  konfiguracja z logu i nowa konfiguracja T1 z `include` oraz `SAVE_CONFIG`
  — przechodzą pełne czytanie konfiguracji.
- Test sprawdza rzeczywiste obiekty obu grzałek, limity 310°C, dokładnie
  osiem czytników ADC oraz powiązanie wentylatorów z T0 i T1.
- Test konfiguracji nie łączy się z drukarką, nie uruchamia ruchu i nie grzeje.
  Fizyczny odbiór kalibracji pozostaje zgodny z poprzednim raportem R15.

Źródła kontroli to `tests/fixtures/native_nozzles_r15_startup.cfg`,
`tests/test_orange_pi_update.py` oraz `ci/check_native_nozzle_startup.py`.
Oba zadania GitHub Actions wykonują teraz pełny test konfiguracji Klippera.

## Aktualizacja Orange Pi

Wykonaj przez SSH:

```bash
git -C "$HOME/inventure-orange-pi" pull --ff-only origin orange-pi-all-axis &&
python3 "$HOME/inventure-orange-pi/install-orange-pi.py" --configure-nozzles
```

Instalator wykrywa aktywny katalog Klippera oraz konfigurację, naprawia
istniejącą sekcję T1 i ponownie uruchamia usługę.

W konsoli Fluidd wykonaj:

```gcode
INVENTURE_VERSION
```

Oczekiwany host to `RC8-CONTROL-R7-UIFAST-PAD-XYZ-R16`. Dla pełnej
kalibracji przy 100°C oczekiwany MCU to `RC8-CONTROL-R7-PAD-THERMAL-R10`,
`heartbeat_timeout_s: 5`, `nozzle_service_target_max_c: 310`,
`nozzle_pad_measurement_band_c: [95, 105]` oraz `baud_match: true`.

Jeśli MCU nadal zgłasza R8, wgraj aplikację `INVENTUR.IVB` z dołączonej
paczki `INVENTURE_MCU_PAD_100C_R10_BOOT17.zip` przez InventureBoot 1.7,
a następnie wykonaj `FIRMWARE_RESTART`. Obraz aplikacji R10 jest identyczny
z poprzednim wydaniem i ma SHA256:

```text
8b3bdc8cf3b6dafb361675012f802db343fe0b2525b089f4398ebfca4401a691
```

Po potwierdzeniu zestawu host R16 / MCU R10 można wrócić do fizycznych
testów kalibracji.
