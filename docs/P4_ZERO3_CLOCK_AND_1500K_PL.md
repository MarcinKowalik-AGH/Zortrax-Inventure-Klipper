# P4 na Orange Pi Zero 3 i test 1,5 Mbaud

STM32F207 RC8 używa USART6 z APB2 60 MHz i próbkowaniem 16×.
H618 używa UART5 z zegara APB2. Przy 24 MHz i całkowitym dzielniku
UART5 prędkości 250000, 500000, 750000 i 1500000 dzielą się bez reszty.
1000000 wymaga dzielnika 1,5, a 2000000 dzielnika 0,75.
Odczytaj zegar bieżącej płyty przed próbą.

```sh
sudo dmesg | grep -Ei '5001400\.serial|ttyS1'
sudo grep -E '(^|[[:space:]])(apb2|bus-uart5)[[:space:]]' /sys/kernel/debug/clk/clk_summary
sudo awk '/^1:/ {print}' /proc/tty/driver/serial
```

`5001400.serial` identyfikuje UART5. `base_baud = 1500000` oznacza
24 MHz w sterowniku Linux. `clk_summary` pokazuje bieżące zegary, jeśli
debugfs jest dostępny.

| Prędkość | Dzielnik UART5 przy 24 MHz | BRR USART6 przy 60 MHz |
|---:|---:|---:|
| 250000 | 6 | 240 |
| 500000 | 3 | 120 |
| 750000 | 2 | 80 |
| 1000000 | 1,5 | 60 |
| 1500000 | 1 | 40 |
| 2000000 | 0,75 | 30 |

[Dokumentacja UART H616, rozdział 9.2](https://linux-sunxi.org/images/2/24/H616_User_Manual_V1.0_cleaned.pdf) ·
[Definicja zegara UART5 w Linuksie](https://github.com/torvalds/linux/blob/master/drivers/clk/sunxi-ng/ccu-sun50i-h616.c)

## Wariant laboratoryjny

Używaj po sprawdzeniu zegara i stabilnego połączenia na obrazie dziennikowym.
Zachowaj dotychczasowy IVB i używaną prędkość BOOT (np. 500000).
Zatrzymaj Klippera, ustaw hosta na 1500000 z rzeczywistą wartością zegara
i sprawdź wygenerowany backup. Dla potwierdzonych 24 MHz polecenia są następujące.

```sh
cd "$HOME/inventure-rc8-repo"
git pull --ff-only
sudo systemctl stop klipper
python3 scripts/inventure_p4_baud_stage.py \
  --printer-cfg "$HOME/printer_data/config/printer.cfg" --baud 1500000 \
  --uart-clock-hz 24000000 --apply --confirm-klipper-stopped
```

Z osobnego artefaktu `P4-LAB-FORCED-1500000` użyj wyłącznie aplikacji
`MCU/INVENTUR.IVB`. Bootloader pozostaw na dotychczasowej prędkości
z `UART on bootwait: OFF`. Po starcie aplikacji lokalny OLED pokazuje
`P4: 1500000 8N1`. Następnie uruchom Klippera. W `INVENTURE_STATUS`
sprawdź `p4_boot.match=true`, `p4_boot.forced_baud=1500000` i liczniki
UART. Test prowadź bez ruchu i grzania. Ten wariant został zweryfikowany
programowo. Stabilność na fizycznym łączu wymaga pomiaru.

Aby wrócić, zatrzymaj Klippera, wgraj dotychczasowy obraz dziennikowy
i przywróć hostowi prędkość z BOOT, np. 500000. Skrypt przyjmuje
`--baud 500000 --apply --confirm-klipper-stopped` lub przywraca
niezmieniony backup przez `--rollback <katalog>`.
