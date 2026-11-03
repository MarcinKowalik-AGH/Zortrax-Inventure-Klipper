# Próba P4 3 Mbaud na Orange Pi Zero 3 i Inventure

Wariant laboratoryjny dla H618, `/dev/ttyS1` (`5001400.serial`),
i STM32F207 V9. Punktem powrotu jest sprawdzone połączenie 1,5 Mbaud.
Próbę przeprowadź bez ruchu i grzania.

| Stan | PLL_PERIPH0 Orange Pi | APB2 / UART5 | P4 aplikacji STM32 |
| --- | ---: | ---: | ---: |
| Punkt powrotu | 600 MHz | 24 MHz | 1,5 Mbaud, `BRR=40` |
| A. Próba zegara | 576 MHz | 48 MHz, `576 / (3 × 4)` | 1,5 Mbaud, dzielnik UART5 = 2 |
| B. Próba łącza | 576 MHz | 48 MHz | 3 Mbaud, dzielnik UART5 = 1, `BRR=20` |

P4 bootloadera InventureBoot pozostaje przy dotychczasowej wartości
(np. 500000) z `UART on bootwait = OFF`. Obrazy MCU dla etapów A i B
ustawiają prędkość aplikacji po wyjściu z InventureBoot.

## A. Sprawdź zegar Orange Pi z obecnym MCU 1,5 Mbaud

1. Zachowaj działającą kartę systemową. Przygotuj surowy obraz `.img` jej
   kopii. Na Macu ustal identyfikator **źródłowej karty microSD** przez
   `diskutil list`. Odczytaj całą kartę, używając jej rzeczywistego
   identyfikatora zamiast `N`.

   ```sh
   diskutil unmountDisk /dev/diskN &&
   sudo dd if=/dev/rdiskN of="$HOME/Armbian-Inventure-TEST.img" bs=4m
   ```

2. Rozpakuj pakiet i wykonaj podgląd na tym pliku `.img` z katalogu paczki.

   ```sh
   python3 BOOT/apply_lab_uboot.py "$HOME/Armbian-Inventure-TEST.img" \
     BOOT/u-boot-sunxi-with-spl-p4-48mhz.bin
   ```

   Po `DRY_RUN_OK` powtórz polecenie z `--apply`. Skrypt sprawdza tablicę
   MBR, offset starego SPL, sumę SPL nowego obrazu oraz odstęp do pierwszej
   partycji. Zapisuje kopię nadpisywanego obszaru. Obraz GPT lub urządzenie
   blokowe wymaga osobnej procedury i zostanie odrzucone.

3. Zapisz zmodyfikowany `.img` na **drugiej karcie microSD** (np. przez
   „Use custom” w Raspberry Pi Imager). Uruchom z niej Orange Pi. U-Boot
   ustawia PLL na początku SPL i zmienia APB2 dopiero przy przekazaniu
   sterowania Linuksowi. Nowy PLL wpływa też na zegary pochodne.

4. Przy wciąż działającym MCU **1,5 Mbaud** odczytaj:

   ```sh
   sudo awk '$1 ~ /^(pll-periph0|psi-ahb1-ahb2|apb2|bus-uart5)$/ {print $1, $5}' \
     /sys/kernel/debug/clk/clk_summary
   ```

   Oczekiwane `pll-periph0 576000000`, `apb2 48000000` i `bus-uart5 48000000`.
   Jeśli system wstał, sprawdź `Ready`, `INVENTURE_VERSION` z `baud_match=true`
   przy 1500000 oraz przewijanie OLED. Przejdź do B po stabilnej pracy przy
   1,5 Mbaud i potwierdzeniu 48 MHz. W razie kłopotu wróć do pierwotnej karty.

## B. Przejdź na 3 Mbaud

Skopiuj rozpakowany katalog `HOST` z Maca na Orange Pi.

```sh
ssh klipper@192.168.3.141 'mkdir -p ~/inventure-p4-3m' &&
scp -r HOST klipper@192.168.3.141:~/inventure-p4-3m/
```

Na Orange Pi, uruchomionym z karty testowej, sprawdź instalację hosta.

```sh
host_kit="$HOME/inventure-p4-3m/HOST"
python3 "$host_kit/scripts/install_host.py" "$HOME/klipper"
```

Po `DRY RUN only` wykonaj w tej samej sesji SSH:

```sh
uart_clock=$(sudo awk '$1 == "bus-uart5" {print $5; exit}' /sys/kernel/debug/clk/clk_summary)
test "$uart_clock" = 48000000 &&
sudo systemctl stop klipper &&
python3 "$host_kit/scripts/install_host.py" "$HOME/klipper" \
  --apply --confirm-service-stopped &&
python3 "$host_kit/scripts/inventure_p4_baud_stage.py" \
  --printer-cfg "$HOME/printer_data/config/printer.cfg" \
  --baud 3000000 --uart-clock-hz "$uart_clock" --apply --confirm-klipper-stopped
```

Wgraj z SD `MCU/INVENTUR.IVB` **do Inventure**, wykonaj `Verify Flash`
i uruchom aplikację. OLED powinien pokazać `P4: 3000000 8N1`. Uruchom usługę
przez `sudo systemctl start klipper`. We Fluidd sprawdź `INVENTURE_VERSION`
i `INVENTURE_STATUS` (`p4_boot.forced_baud=3000000`, `p4_boot.match=true`)
oraz `INVENTURE_OLED_STATUS` (`host_frame_acknowledged=true`). Porównaj
przyrost `bytes_invalid` i `bytes_retransmit` w `klippy.log` w ciągu 5 minut
bezczynności i przewijania. W razie problemu zachowaj końcówkę logu.

## Powrót

Zatrzymaj usługę, wgraj dotychczasowy `INVENTUR.IVB` 1,5 Mbaud (jego kopia
jest w `MCU_ROLLBACK_1M5`), a następnie
uruchom Orange Pi z pierwotnej karty, która ma działający host i
`baud: 1500000`. Aby wrócić do 1,5 Mbaud na karcie testowej, wykonaj na niej
skrypt `inventure_p4_baud_stage.py` z `--baud 1500000`, rzeczywistym zegarem
UART5 i zatrzymaną usługą, przed jej ponownym uruchomieniem.

## Odtworzenie kompilacji

U-Boot jest oparty na `v2026.07` i `orangepi_zero3_defconfig`. Zastosuj
`zero3/lab/u-boot-2026.07-zero3-p4-apb2-48mhz.patch` przez `patch -p1`.
Dla TF-A `v2.13.0` zbuduj `make PLAT=sun50i_h616 DEBUG=0 bl31`.
W źródłach U-Boot wykonaj `make orangepi_zero3_defconfig` i
`make BL31=/ścieżka/do/bl31.bin`. Użyj cross-kompilatora AArch64.
Kompilacja MCU korzysta z `ci/prepare_kit.py`, a następnie z polecenia
`scripts/build_firmware_on_mac.py --baud 3000000` w przygotowanym katalogu.

- [Źródła U-Boot v2026.07](https://github.com/u-boot/u-boot/tree/v2026.07)
- [Źródła TF-A v2.13.0](https://github.com/ARM-software/arm-trusted-firmware/tree/v2.13.0)
