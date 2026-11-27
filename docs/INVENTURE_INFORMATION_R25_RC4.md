# Inventure R25 RC4 — ograniczenie ruchu UART podczas bazowania

| Element | Wersja |
| --- | --- |
| Instalator | R25.4 |
| Ochrona komunikacji | `RC8-COMMUNICATION-GUARD-R25.3` |
| Info | zachowane `RC8-INFORMATION-R25.2` |
| Rdzeń hosta | zachowany R38.6 |
| MCU | istniejące `RC8-CONTROL-R7-HOME-R25-INFO-OLED` |

Aktualizacja dotyczy hosta. Nie wymaga ponownego wgrywania MCU. Odczyt
fabrycznej tożsamości i czasu pracy w RC3 został potwierdzony przez operatora.
Nowa awaria Home Z jest osobnym zdarzeniem.

## Co ustalono z nowego logu

Ostatnia sesja rozpoczyna się 27 listopada 2026 o 10:23:43 czasu polskiego.
Przygotowanie T0 zakończyło się wynikiem `success: true`, `HOME_PREPARE`.
Następnie wykonywał się natywny zjazd Z do dolnej krańcówki PE12. MCU przerwało
pracę z błędem **`Timer too close`**, przed pierwszym etapem PC14. W tej
nieudanej próbie nie rozpoczęła się pętla pomiarowa kontakt–rozwarcie.

W zrzucie kolejki pierwszy krok jednego z bloków Z ma zegar
`79688029967`, a zatrzymanie ma zegar `79688430651`. Różnica to 400684 takty,
czyli około **3,34 ms** przy nominalnych 120 MHz. Polecenia kolejnych bloków
wychodziły co około 50 ms z zapasem około 98 ms. W końcówce logu potwierdzenia
UART przestają nadążać za nadawcą; retransmisje dochodzą do 64890 bajtów.

Obciążenie hosta w ostatnich statystykach wynosiło 0,54, a dostępna pamięć
około 1,8 GB. Sam komunikat Klippera o możliwym przeciążeniu komputera nie
stanowi tu rozpoznania przyczyny. Log wskazuje spóźnienie komunikacji, lecz
nie rozstrzyga, czy pierwotnym źródłem było kolejkowanie sterownika UART,
retransmisja, przewód, zasilanie czy inne opóźnienie. Nie ma podstaw, aby
przypisać tę awarię zmianie liczby dotknięć PC14.

W tej samej historii są dwie wcześniejsze udane pętle po 102 cykle.
Ich powodzenie nie potwierdza ostatniej próby ani niezawodności po aktualizacji.

## Zakres poprawki

Osobny moduł obserwuje zdarzenia `homing:homing_move_begin` i
`homing:homing_move_end` istniejącego Klippera. Na czas natywnego ruchu
bazującego wstrzymuje nowe porcje pikseli OLED, również z callbacku ACK.
Pozostawia ostatni obraz fizycznego ekranu. Obsługa przycisków, podtrzymanie
sesji ekranu co 500 ms, oddzielne heartbeat MCU, krańcówki i zatrzymanie
pozostają u dotychczasowych wykonawców. Podgląd Fluidd może odświeżać swój
bufor. Kolejka obrazu nie tworzy aktywnej pętli timera podczas pauzy.

Po zakończeniu ruchu grafika wraca do dotychczasowego nadawcy. Oznacza to
świadomą pauzę animacji na fizycznym OLED podczas natywnych dojazdów do
krańcówek. Grzanie, wybór T0, czyszczenie i lokalna pętla PC14 zachowują
dotychczasową prezentację.

Okresowy synchronizator Info odkłada cały odczyt/synchronizację licznika
podczas Home, ruchu, procedury SERVICE i druku. Odczyt zapisanych danych i
niezależne naliczanie/zapisywanie czasu druku na hoście pozostają czynne.
Nie wykonuje dodatkowego zapytania NVM tylko po to, żeby ustalić, że zapis
w tym momencie jest niedozwolony.

Nie zmieniono żadnego bajtu czterech wykonawców Home:

| Plik | SHA256 |
| --- | --- |
| `inventure_operator_home.py` | `79dc8b712cf54f0e86b67936d1569690071345e8662a29d4ae026236acc53352` |
| `inventure_native_z.py` | `4e6643642cc88868a0b46cc37abbf9c3d9ae30850aed0352e51ddd03d2fa380a` |
| `inventure_mcu.py` | `2f9216e819c5d9ca34330db87bbe4032eacbde2dc73aa05ec51316532a0dfe6b` |
| `inventure_z_home.py` | `030ab6cf028c8321fa877d0c7d553162061722d66f2a7af14b04f452460621cd` |

Zachowano także `inventure_home_ui.py`, silniki trwałego zapisu Info i MCU.
Aktualizacja RC3 zmienia tylko adaptery OLED/Info, dodaje moduł ochrony
komunikacji i aktualizuje manifest. Weryfikowany manifest poprzedniego RC3:
`8b738e864f877e176094fd02d698a3e4050bf2dfbc27ab1e1703bb6a3ac3dba3`.

## Weryfikacja i granice wyniku

Testy wykonują rzeczywisty nadawca OLED i jego callback ACK z czterema
pakietami w locie. Podczas dziesięciosekundowego symulowanego bazowania
nie wychodzą nowe piksele, działają przyciski i podtrzymanie, a timer nie
wpada w aktywną pętlę. Sprawdzane są zagnieżdżone ruchy, błąd/rozłączenie,
wznowienie obrazu, odroczenie Info oraz dokładna aktualizacja i wycofanie RC3.
Zachowane testy obejmują wspólne Home z G28, makr Fluidd i menu OLED oraz
interlocki, instalację i tożsamość firmware.

To poprawka ograniczająca dodatkowy ruch na łączu, a nie potwierdzenie
fizycznego usunięcia źródła opóźnień UART. Po instalacji pozostaje sprawdzenie
Home na drukarce. Nie wyłączono błędu terminów ani zabezpieczeń MCU.

## Instalacja

Na bezczynnej drukarce, z wyłączonymi celami grzania, wykonaj raz we Fluidd
`FIRMWARE_RESTART`. Po Ready, przed Home i grzaniem, w terminalu Orange Pi:

```bash
cd ~/inventure-src &&
git fetch origin --tags &&
git switch --detach inventure-information-r25-rc4 &&
python3 install-new-host.py --repair-home-procedures --serial /dev/ttyS5 --baud 750000 --apply
```

Instalator ma potwierdzić załadowanie ochrony komunikacji w obu adapterach.
Bez ruchu odczytaj we Fluidd:

```gcode
INVENTURE_INFO
INVENTURE_OLED_STATUS
```

Oba statusy mają zawierać `communication_guard_release` równe
`RC8-COMMUNICATION-GUARD-R25.3`. Status OLED pokazuje też
`native_homing_pixels_paused`, `native_homing_quiet_count` i
`native_homing_quiet_seconds`. Dane tożsamości i czasu pracy nie są zerowane.
