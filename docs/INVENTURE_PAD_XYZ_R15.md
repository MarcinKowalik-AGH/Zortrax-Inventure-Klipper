# Zortrax Inventure R15 / MCU R10

R15 naprawia parkowanie w grzanych procedurach T0 i T1. CLEAN i PURGE
kończą się powrotem ze szczotki nad kosz. Dotyczy to także długiego AUTO
T1 w profilu SERVICE. Grzane procedury kasetowe dojeżdżają nad kosz
przed pierwszym włączeniem grzałki.

Wydanie zawiera raport kontroli MCU R10 i hosta R14 oraz nowe regresje
parkowania. Weryfikacja obejmuje 522 testy hosta, osiem zestawów C
oraz konfigurację hosta i natywnego Z wobec słownika dostarczonego MCU R10.
[Pełny raport](https://github.com/MarcinKowalik-AGH/Zortrax-Inventure-Klipper/blob/orange-pi-all-axis/docs/AUDYT_MCU_R10_HOST_R14_2026-11-17.md).

Na Orange Pi wykonaj przez SSH poniższe polecenia.

```bash
git -C "$HOME/inventure-orange-pi" pull --ff-only origin orange-pi-all-axis &&
python3 "$HOME/inventure-orange-pi/install-orange-pi.py" --configure-nozzles
```

Host R15 obsługuje dotychczasowe MCU `RC8-CONTROL-R7-PAD-THERMAL-R10`.
Dołączony `INVENTUR.IVB` ma tę samą sumę co obraz R10 z wydania R14.
Przy aktualizacji ze starszego MCU wgraj aplikację przez InventureBoot 1.7
po aktualizacji hosta.

W Fluidd wykonaj `FIRMWARE_RESTART`, a po połączeniu `INVENTURE_VERSION`.
Oczekiwane są host `...PAD-XYZ-R15`, MCU `...PAD-THERMAL-R10`,
`heartbeat_timeout_s: 5`, `nozzle_service_target_max_c: 310`,
`nozzle_pad_measurement_band_c: [95, 105]` i `baud_match: true`.

Pełny pomiar pada przy 100°C i fizyczny odbiór poprawionych trajektorii
pozostają do sprawdzenia na drukarce. Raport podaje wyniki wcześniejszych
prób i brakujące potwierdzenia dla poszczególnych procedur.
