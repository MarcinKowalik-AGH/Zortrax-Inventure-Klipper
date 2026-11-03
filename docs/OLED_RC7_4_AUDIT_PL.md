# Inventure RC7.4 OLED FAST — audyt i wdrożenie, 2 listopada 2026

Opis dotyczy historycznej wersji RC7.4 z [commita e370d58](https://github.com/MarcinKowalik-AGH/Zortrax-Inventure-Klipper/tree/e370d5899b68a48274179349428285cb59d5bfdd).
Dla bieżących paczek użyj [instrukcji RC8](BUILD_AND_INSTALL_PL.md).

## Baza i źródła wymagań

Baza GitHub: `216f138c898189f1235de26a2e3ef60886f0a9c0`,
`MarcinKowalik-AGH/Zortrax-Inventure-Klipper`, gałąź `main`.
Odczytano aktualny kod, workflow, niezmienny snapshot RC7.2 oraz rzeczywisty
pakiet `INVENTURE_RC7_3_OLED_FAST_M300_STYLE_HOST_ONLY_2026-11-02.zip`
(SHA-256 `309e20c50409dc23b88267ff399f357a1724db80da83081d9ec2fe3ab4e54a72`).

Obowiązujące dokumenty Google Drive:

- [ZORTRAX_M300_POPRAWKI_TIMING_TRANSFER_INVENTURE_2026-11-02.md](https://drive.google.com/file/d/1lA0C82j_Qn3W3LZ6LxM7boSnTxHTOy6X/view)
- [M300_R14_4_TIMING_POPRAWKI_I_TRANSFER_DO_INVENTURE_2026-11-02.md](https://drive.google.com/file/d/1imio9Rz50VVo8fqqaquB5CXmpiRo1y8e/view)

## Wynik audytu i macierz decyzji

| Wymaganie | Stan przed zmianą | Wynik RC7.4 |
|---|---|---|
| Odczyt czasu i ADC pod tym samym IRQ lock | Już obecny: `guard_check`, `INVENTURE_RC7_ADC_SNAPSHOT_ABI=1`; ARM również sprawdza sensory w sekcji IRQ | Zachowany; brak fikcyjnego ponownego portowania M300 |
| Początek epoki lokalnego OLED | Lokalny renderer ma już odroczony start swojej epoki | Zachowany |
| Krótkie SPI, przerwania podczas transmisji | Hostowe porcje do 32 B; sprzętowe burst 8/32 B; IRQ odblokowane podczas SPI | Zachowane |
| Próbkowanie V9 20 kHz / 50 µs | `sample_event`, `SAMPLE_TICKS`, zależne quiet/fan/guard; osobne kontrakty feeder/lock/assist | Bez modyfikacji okresu ani liczników |
| Margines 200 µs z M300 | Nie pasuje do próbkowania 50 µs | Nie przeniesiono do samplera; brak dowodu pozwalającego zmienić tę semantykę |
| Osobny pakiet UI RC7.3 | Brak w buildzie z GitHuba | Trzy moduły RC7.3 włączone do realnego stagingu, regresji i paczki Actions |
| Menu OLED | Cztery rzędy i stały nagłówek | Pięć rzędów y=0/12/24/36/48; przypięty Back; status Klipper jako ekran początkowy |
| Render statycznego ekranu | Ponawiany co 0,25 s | Cache według treści/generacji; powrót z komunikatu unieważnia poprzedni widok |
| Interwały OLED | Domyślny refresh 0,5 s, w znanym printer.cfg chunk 0,020 s | Domyślnie refresh 0,2 s, chunk 0,002 s; instalator poprawia znane wcześniejsze konfiguracje |
| Wybudzenia hosta | `_tick` co 2 ms również przy nieruchomym ekranie | Co 2 ms podczas transmisji i przytrzymania; do 50 ms w idle, z wcześniejszymi terminami usług/renderu i natychmiastowym wake po przycisku/poleceniu |
| Integralność wydania | Snapshot i czytelne pliki były osobnymi kopiami | Hash-pin czterech modułów OLED, synchronizacja overlay testów i kontraktów, odmowa rozbieżności MCU/serial |

RC7.4 zmienia cztery moduły hosta OLED. Zachowuje ABI3 płyty i ABI2 OLED,
jedną operację oczekującą na ACK, tokeny/generacje, idempotentne powtórzenia,
timeout, dzierżawę MCU oraz zatrzask shutdown. Nie zmienia bootloadera,
P4-PERSIST, UART, kinematyki, pinów, STEP, limitów ruchu ani nastaw termicznych.
Nie dodaje ograniczenia suwaka 150% Fluidd.

## Jednostki i rzeczywista przepustowość

`refresh_interval: 0.2` oznacza **0,2 s = 200 ms**, a nie 0,2 µs.
`chunk_interval: 0.002` oznacza **2 ms**. Zmiana przycisku może ominąć
okresowe oczekiwanie, lecz nadal respektuje aktualną operację i jej ACK.
Pełna klatka ma 8192 B; jej czas przesłania zależy od UART, ACK, SPI i
obciążenia. Ustawienie 0,2 s nie gwarantuje pełnej klatki w tym czasie.

Z rzeczywistego renderera wyliczono zmianę kursora Back → Models:
okno `(1,21,2,3)`, **84 B danych graficznych, 8 operacji** zamiast pełnej
klatki. Liczba nie obejmuje narzutu ramek protokołu UART. To wynik obliczenia
offline; nie jest optycznym pomiarem reakcji na drukarce.

## Walidacja

| Sprawdzenie | Wynik lokalny |
|---|---|
| Integralność 26 overlayów i staging właściwego pinned Klippera | PASS |
| Regresja hosta P01–P17, RC7.3, nowe scenariusze harmonogramowania | 588 uruchomionych: 587 PASS + 1 SKIPPED prywatnego obrazu OEM |
| Akceptacja Zero 3 / Fluidd / walidator słownika | 28/28 PASS |
| Integracja snapshot → build oraz instalator | 11/11 PASS |
| C: ring UART, OLED przy 2 MHz i 500 kHz, lokalny ekran, boot journal i USART6 | 10 zestawów wykonanych, PASS |
| `git diff --check`, parsowanie YAML workflow | PASS |

Nowe testy pokrywają deadline usług, render co 0,2 s, mniejszą liczbę
wybudzeń w idle, wake przyciskiem, komunikat po shutdown, brak ACK,
odpowiedź ze starej generacji, cache i jego unieważnianie, odrzucenie
nieznanego źródła przed zapisem oraz bitowy rollback.

Lokalny runtime: Python 3.12, greenlet 3.2.4, Jinja2 3.1.6. Brak lokalnego
GNU ARM/Clang. GitHub Actions używa przypiętego Python 3.11 i zależności
oryginalnego zestawu oraz Arm GNU 14.2.Rel1; wykonuje pełne linkowanie,
kontrolę ELF/wektorów/słownika i kontenera IVB. Aktualny wynik tej kompilacji
jest widoczny przy konkretnym commicie/PR. Zielony build nie jest testem
fizycznego OLED, ruchu, termiki ani odporności Inventure pod obciążeniem.

## Aktualizacja działającego hosta

Po pobraniu tej gałęzi repozytorium, w jego katalogu:

```sh
python3 scripts/install_oled_rc74.py --klipper ~/klipper \
  --printer-cfg ~/printer_data/config/printer.cfg
```

To dry run. Instalator rozpoznaje dokładne SHA RC7.2, RC7.3 i RC7.4.
Pokazuje plan i diff konfiguracji. Nieznane modyfikacje wymagają osobnego
porównania źródeł; nie zastępuj ich ręcznie plikami z paczki.

Po poprawnym planie, przy niepracującej drukarce:

```sh
sudo systemctl stop klipper
python3 scripts/install_oled_rc74.py --klipper ~/klipper \
  --printer-cfg ~/printer_data/config/printer.cfg \
  --apply --confirm-service-stopped
sudo systemctl start klipper
```

Zapisz ścieżkę `BACKUP` z wyniku. Instalator zachowuje niepowiązane
ustawienia. Jeżeli wybrano wcześniej `oem_start_screen: menu`, zachowuje tę
preferencję. Aktualizacja nie wymaga ponownego wgrywania MCU.

W artefakcie GitHub Actions uruchamiaj analogiczne polecenie z katalogu
`HOST`, używając `python3 install_oled_rc74.py ...`.

W Fluidd sprawdź `INVENTURE_OLED_STATUS` i `INVENTURE_MENU_STATUS`.
`ui_transport` raportuje `host_release=RC7.4-OLED-FAST`, interwały w sekundach,
`rendered_frames` oraz `render_cache_hits`. Oczekiwane: wzrost cache hits przy
niezmienionym ekranie, brak nowych bitmap do przesyłania w idle, brak błędów
ACK i poprawny powrót Back. Zmierz czas reakcji oraz UART pod obciążeniem
według planu I0–I7/I0–I10 z odczytanych dokumentów Drive.

Wycofanie, przy zatrzymanym Klipperze:

```sh
python3 scripts/install_oled_rc74.py --klipper ~/klipper \
  --printer-cfg ~/printer_data/config/printer.cfg \
  --rollback '/dokładna/ścieżka/BACKUP' --confirm-service-stopped
```

Rollback odmawia nadpisania plików zmienionych niezależnie po aktualizacji.

## Odtworzenie builda

```sh
python3 ci/prepare_kit.py --destination ../inventure-rc74-kit
python3 ../inventure-rc74-kit/scripts/verify_release.py
python3 -m unittest discover -s tests -p 'test_*.py' -v
```

Wymagane zależności: `REQUIREMENTS_MAC_TEST.txt` odtworzonego zestawu
(dla przypiętego Python 3.11). `prepare_kit.py` akceptuje tylko nowy katalog.
Snapshot RC7.2 pozostaje niezmienny. Zastosowanie zmian do zestawu sprawdza
wszystkie wejścia przed pierwszym zapisem i aktualizuje wyłącznie świadomie
zmienione kontrakty OLED; ochrona innych modułów pozostaje aktywna.
