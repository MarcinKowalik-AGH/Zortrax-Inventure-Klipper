# Inventure R17 / MCU R11 — zapis pierwszej przyczyny zatrzymania pada

Próba z `klippy(13).log` zakończyła się ochronnym zatrzymaniem sesji MCU
podczas `XYZ_SCAN_T0`. Homing XY, odniesienie dolne Z, wybór T0,
nagrzanie i czyszczenie T0 zostały wykonane. Podczas podjazdu stołu
nie wystąpił sygnał styku dyszy. MCU zgłosił `reason=4`.

R10 przypisuje ten sam kod do kilku warunków: temperatury poza pasmem,
błędu lub nieaktualnego ADC, błędu grzałki i wygaśnięcia jej czasowego
zezwolenia. Temperatury hosta 101,05°C i 102,99°C po zatrzymaniu nie
pozwalają wybrać jednego z tych powodów. MCU sprawdza pojedyncze
konwersje, a host pokazuje średnią z trzech konwersji. R10 nie zachowuje
próbki, która uruchomiła ochronę. Wtórny komunikat o trybie SERVICE
wynika z wyłączenia sesji przez MCU.

## Zakres wydania

R17/R11 naprawia brak zapisu pierwszej przyczyny zatrzymania. Przyczyna
przerwania próby fizycznej pozostaje do ustalenia z nowego rekordu.
To wydanie diagnostyczne; nie stanowi potwierdzenia udanej kalibracji.

MCU R11 zapisuje przed STOP stan ośmiu czujników: surowe ADC,
temperatury użyte w ochronie, wiek i ważność próbek, aktywne limity,
kontekst pasma pomiarowego oraz kody wyjść obu grzałek. Późniejsze
próbki ADC i polecenia wyłączenia wyjść zachowują ten rekord.
Udany START nowej sesji usuwa poprzedni zapis. Odczyt sprawdza token
sesji i nie odnawia zezwolenia na ruch lub grzanie.

Host R17 automatycznie dodaje zapis do `ACTION=STATUS`, w polu
`details.mcu_guard`, oraz opisuje wykryty warunek w komunikacie błędu.
Przykłady opisów:

| Opis | Znaczenie |
| --- | --- |
| `T1_measurement_above_band` | Próbka T1 użyta przez MCU przekracza górną granicę pasma pomiarowego |
| `T0_measurement_below_band` | Próbka T0 jest poniżej dolnej granicy |
| `T1_adc_stale` | Wiek próbki T1 wyniósł co najmniej 1000 ms |
| `PC1_adc_invalid` | MCU uznał ADC czujnika PC1 za nieprawidłowe |
| `T1_heater_lease_expired` | Wygasło czasowe zezwolenie na grzanie T1 |

Warunki mogą wystąpić równocześnie; pełny rekord zawiera wszystkie
próbki i kody, także po powrocie temperatury do normalnej wartości.

Warunki ochronne i trajektorie są takie same jak w R16/R10: pomiar
100°C w paśmie 95–105°C, maksymalne nastawy 310°C, timeout komunikacji
5 sekund, dotychczasowe limity Z i tolerancja krawędzi 0,05 mm.
Host zachowuje zgodność z R10, ale nowy zapis wymaga MCU R11.

## Weryfikacja

- 527 testów hosta, w tym odróżnienie temperatury poza pasmem, błędu
  i nieaktualności ADC oraz wygaśnięcia zezwolenia na grzanie.
- Test rzeczywistego czytania konfiguracji przez przypięty Klipper,
  z naprawą T1, plikami `include` i stopką `SAVE_CONFIG`.
- Osiem zestawów C uruchamiających źródła MCU na modelu rejestrów.
  Nowe przypadki wyzwalają po kolei 106°C, 94°C, nieaktualny ADC,
  nieprawidłowy ADC i wygaśnięcie grzania. Każdy nadal zatrzymuje
  ruch i obie grzałki. Odczyt zachowuje pierwotną próbkę po STOP
  i powrocie do 100°C oraz odrzuca token innej sesji.

## Aktualizacja i próba

Najpierw zaktualizuj hosta przez SSH:

```bash
git -C "$HOME/inventure-orange-pi" pull --ff-only origin orange-pi-all-axis &&
python3 "$HOME/inventure-orange-pi/install-orange-pi.py" --configure-nozzles
```

Następnie wgraj `INVENTUR.IVB` z paczki
`INVENTURE_MCU_PAD_GUARD_R11_BOOT17.zip` przez InventureBoot 1.7.
W Fluidd wykonaj `FIRMWARE_RESTART`, potem `INVENTURE_VERSION`.
Oczekiwane są host `...PAD-XYZ-R17`, MCU `...PAD-THERMAL-R11`
i `factory_guard_diagnostic_abi: 1`.

Przy pustych dyszach powtórz tę samą próbę:

```gcode
INVENTURE_CONTROL MODE=SERVICE
ZORTRAX_NOZZLE_PAD_CALIBRATE ACTION=START MODE=FULL SCAN=SWEEP PREP=EMPTY ACK=PAD_GEOMETRY_MEASURED T0_PAD_X=81.70 T0_PAD_Y=68.20 T1_PAD_X=70.80 T1_PAD_Y=68.70 T0_Z_MAX=139.50 T1_Z_MAX=137.95 PROCEDURE_TIMEOUT=3600
```

Po zakończeniu lub zatrzymaniu odczytaj:

```gcode
ZORTRAX_NOZZLE_PAD_CALIBRATE ACTION=STATUS
```

Po zatrzymaniu zachowaj ten wynik przed rozpoczęciem nowej sesji.
Pełny rekord `details.mcu_guard` pozwoli ustalić konkretny warunek,
zamiast wnioskować z temperatury wyświetlanej po fakcie.
