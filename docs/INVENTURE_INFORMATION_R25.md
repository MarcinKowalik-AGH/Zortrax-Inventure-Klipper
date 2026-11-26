# Inventure R25 RC1 — Info, numer seryjny i trwały czas druku

Zakładka **Information** odtwarza aktywny, wspólny interfejs M300: OLED i Fluidd
korzystają z tych samych pól, kolejności, wyborów i powrotów. Zatwierdzone
wykonawcy bazowania X/Y/Z, przełączania T0 i czyszczenia pozostają bez zmian.

| Element | Wersja |
| --- | --- |
| MCU | `RC8-CONTROL-R7-HOME-R25-INFO-OLED` |
| Rdzeń hosta i wykonawcy Home | `RC8-CONTROL-R7-HOMING-R38.6`, profil `operator-home-r24` |
| Nowa funkcja Info | `RC8-INFORMATION-R25.1` |
| Bootloader / P4 | istniejący InventureBoot 1.7, `/dev/ttyS5`, 750000 baud |

## Ekrany — jak w M300

| Information → pozycja | Kolejność wierszy po przypiętym Back |
| --- | --- |
| Firmware version | Host, MCU, Klipper, OEM reference |
| Printer information | **Model**, **Hardware version**, **Serial number**, **Total printing time** |
| Help | **Errors**, adres projektu i dokumentacja |
| Help → Errors → szczegół | Katalog 14 istniejących komunikatów Inventure i właściwe wskazówki. Tylko potwierdzony kod kabla **#138** jest opisany jako numer OEM; numery błędów M300 nie zostały skopiowane. |

Kliknięcie Back i fizyczny POWER/Back wracają do właściwego rodzica. Powrót
z informacji przywraca zaznaczoną pozycję Information; szczegół błędu wraca
do tej samej pozycji katalogu, a katalog do Help/Errors. Back ma znacznik
skierowany w lewo. Zasady przewijania pozostałych menu są zachowane.

Istniejące `INVENTURE_INFORMATION` otwiera dział Information. Nowe przyciski
`INVENTURE_PRINTER_INFORMATION` i `INVENTURE_FIRMWARE_VERSION` otwierają
odpowiednie ekrany w grupie **ZORTRAX INVENTURE**. `INVENTURE_INFO` zwraca
ten sam zestaw danych jako JSON. Samo otwieranie, renderowanie i przewijanie
odczytuje pamięć hosta; nie zleca pomiaru, ruchu, grzania ani zapisu Flash.
Otwarty ekran odświeża pola z opublikowanego, trwałego punktu kontrolnego,
bez oczekiwania na zapis pliku. Odświeżenie nie zmienia kursora, tokenu Back
ani czasu wygaszania OLED.

## Źródła danych Inventure

Numer seryjny pochodzi z **OTP rzeczywistego MCU**. Odtworzono dokładnie
czytnik oryginalnego firmware r0323: granicę bloków OTP, cztery bajty numeru
w kolejności oryginalnego formatu `Z%02X%02X%02X%02X` i oddzielny bajt wersji
sprzętu `0xE0..0xEF`. UID STM32 wiąże historię z konkretnym kontrolerem;
nie jest przedstawiany jako numer seryjny. Nie zapisujemy OTP ani UID.
Nieodczytane dane są oznaczane jako `Unavailable`.

Fabryczny czas drukowania jest rekordem **0**, uint32 w **minutach**, w dwóch
bankach r0323: `0x08008000` i `0x0800C000`, po 16 KiB. Są to sektory 2 i 3
STM32F207 1 MiB. Pozostałe 52 rekordy, w tym nieopisane ustawienia i bajty
kalibracji, zachowują dokładnie te same wartości podczas aktualizacji ID0.
Kod odmawia zapisu przy innej geometrii/procesorze, niezgodnym numerze,
zmienionym CRC ustawień, cofniętym liczniku albo nierozstrzygniętej historii.

Dowód: [`oem_information_evidence.json`](../restored/information_r25/oem_information_evidence.json).
Oryginalny callback Printer information: `0x080161F0`; czytnik OTP:
`0x0801C928`/`0x0801C944`; format numeru: `0x0802FF4E`.
Kod czytnika jest porównywany z wykonaniem oryginalnych instrukcji ARM
w Unicorn, a nie z domniemanym formatem numeru.

## Liczenie i zapamiętywanie czasu

To **czas aktywnego drukowania modelu**, zgodnie z M300, a nie czas włączenia
Orange Pi lub samych grzałek. Standardowa ścieżka Klippera rozpoczyna pomiar
po pierwszym pomyślnie przyjętym ruchu XYZ z dodatnią ekstruzją podczas
aktywnego wydruku z pliku. Samo bazowanie, nagrzewanie i ręczna ekstruzja
poza wydrukiem nie uruchamiają zegara. Pauza i zakończenie uwzględniają
jeszcze ruchy oczekujące w kolejce; czas oczekiwania na operatora po pauzie
jest wyłączony. Anulowany wydruk zachowuje już wykonany czas. Wznowienie
nie rozpoczyna drugiej historii tego samego zadania.

Zegar monotoniczny i reguły rozliczania minut przeniesiono z M300. Host
zapisuje punkty kontrolne co **15 sekund**, przy zatrzymaniu i rozłączeniu.
Aktualizacja używa pliku tymczasowego, `fsync`, atomowej zamiany, sumy SHA-256
i dwóch kopii stanu. Nie wykonuje zapisu po każdym G1. Przy nagłym odcięciu
zasilania niezapisany fragment może wynieść do jednego interwału kontrolnego;
restart z normalnym zamknięciem zachowuje końcowy odcinek.

Główne pliki, obok `printer.cfg`:

- `inventure_information.json` i `.checkpoint` — tożsamość, sekundy druku
  Klippera, liczba ukończonych wydruków;
- `inventure_information.json.runtime-sync` i `.checkpoint` — trwałe intencje
  i potwierdzenia rozliczenia minut z MCU.

Nie wolno przenosić historii do innego MCU: porównywane są UID i fabryczny
numer. Uszkodzony lub sprzeczny stan jest zgłaszany; licznik nie jest
automatycznie zerowany. Numer i potwierdzone dane można wyświetlić z pamięci
trwałej, zanim ponowny odczyt MCU zostanie zakończony; pole źródła wskazuje,
czy użyto świeżego odczytu, czy zapisanej kopii.

Łączny czas jest liczony tak samo jak w M300:

`fabryczne minuty × 60 + sekundy Klippera jeszcze nierozliczone w MCU`.

Po zapisaniu pełnych minut do MCU odejmowana jest dokładnie ta sama liczba
sekund z części nierozliczonej. Utrata odpowiedzi po zapisie lub restart
między zapisem MCU a potwierdzeniem hosta nie dolicza minut ponownie.
Ułamki minuty pozostają w trwałym stanie hosta dla kolejnych wydruków.

## Zapis fabryczny i zachowanie silników

Pełna intencja zapisu jest trwała **przed** wysłaniem polecenia do MCU.
Drugi bank otrzymuje kompletną kopię bieżących ustawień ze zmienionym ID0;
dopiero po weryfikacji staje się aktywny. Stary bank jest kasowany na końcu.
Przerwaną operację można dokończyć z zachowanej intencji i pełnej kopii.
Nie ma zgody na zgadywanie, która obca historia ustawień jest właściwa.

Zapisy są dopuszczone wyłącznie po niezależnym sprawdzeniu hosta i MCU:
brak wydruku/pauzy, pusta kolejka, wyłączone grzałki i oczekujące PWM,
brak procedury SERVICE, testu, rygla lub transportu oraz pełny stan OFF MCU
z wyłączonym PE10. **Nie zwalniamy silników po Home i nie przełączamy
właściciela po to, żeby zapisać licznik.** Przy trzymanych osiach lub nadal
aktywnym NATIVE zapis czeka jako `AWAITING_IDLE`; sekundy są już zachowane
na hoście. Pełne OFF po zwykłym restarcie MCU daje następne okno zapisu.
Nie wymuszamy automatycznego restartu.

Zapis Flash wykonuje sprawdzona w ELF funkcja w SRAM. Podczas bezpiecznego
okna OFF zachowywane są względne terminy oczekujących timerów; po operacji
przywracane są te same callbacki. Normalna obsługa timerów, Home i sterowanie
drukowaniem nie wchodzą w tę ścieżkę. Bootloader, P4, OTP, option bytes
i obszar aplikacji nie są celami tego zapisu.

Przy braku dawnych potwierdzeń dla już istniejącej historii wymagane jest
świadome ustalenie wcześniej rozliczonych minut. Polecenie
`INVENTURE_COUNTER_RECONCILE COMMITTED_MINUTES=...` aktualizuje tylko
potwierdzenia hosta, z kontrolą zgodności i limitów. Nie jest potrzebne przy
zwykłej instalacji od RC7: nowe liczniki Klippera zaczynają od zera, a
fabryczny przebieg pozostaje odczytany z MCU.

## Weryfikacja i granice

- 66 oryginalnych rekordów menu, 133 pozycje statyczne i 221 pozycji dialogów,
  w tym nowe Help/Errors: ikony, Back i wspólne wybory;
- rzeczywisty parser Klippera, literalne makra Info i fizyczne wybory OLED,
  zgodność czterech pól, podglądu i powrotów bez dostępu do aktuatorów;
- oryginalne instrukcje ARM czytnika OTP jako niezależny wzorzec;
- każdy punkt kasowania/programowania banku, także częściowy zapis,
  z ponowieniem i sprawdzeniem wszystkich 53 rekordów;
- stracone odpowiedzi, restart, przerwany zapis potwierdzenia, cofnięte lub
  sprzeczne dane, obcy UID, pauza/wznowienie, zakończenie i kolejka ruchu;
- rzeczywista bramka MCU sprawdzona dla każdego aktywnego właściciela,
  trzymanych silników i oczekujących kroków; timery także przez przepełnienie;
- aktualizacja i dokładne wycofanie od RC1–RC7 oraz pełna instalacja;
- poprzednie 800 żądań Home i 184 odmowy, regresje MCU oraz kompilacja ARM.

Cztery pliki wykonawcze hosta pozostają identyczne z RC7:
`inventure_operator_home.py`, `inventure_native_z.py`, `inventure_mcu.py`
i `inventure_z_home.py`. MCU dodaje odrębne polecenia Info i transakcję
licznika; dotychczasowe sekwencje ruchu i przekazania właściciela są zachowane.

Wszystkie nowe próby są **programowe**. Nie odczytano jeszcze numeru ani
licznika fizycznej drukarki w R25 i nie wykonano na niej nowego zapisu Flash.
Galeria `R25_INFORMATION_OLED.png` pokazuje rzeczywisty renderer z jawnie
symulowanymi wartościami — `Z1234ABCD` jest wartością testową.

Odniesienie M300: gałąź `orange-pi`, commit
`1f9d77432cbbdc9ae69298b6e4815b4bd819e963`. Role ekranów z
`m300_settings.py`, zegar z `m300_runtime.py`, protokół rozliczenia z
`m300_counter_sync.py`; [sumy źródeł](../restored/information_r25/m300_reference.json).

## Instalacja

Odczyt OTP i fabrycznych banków wymaga **nowej aplikacji MCU R25**.
Dotychczasowy R24 nie ma tych poleceń. Nowy host może nadal wystartować
z R24, ale wtedy zgłasza `MCU_INFO_UPDATE_REQUIRED` i nie zapisuje Flash.

Przy bezczynnej drukarce wyłącz cele grzania. Wgraj `INVENTUR.IVB` z wydania
`inventure-information-r25-rc1` dotychczasową procedurą InventureBoot 1.7.
Zachowaj ustawione P4 750000. Po ponownym uruchomieniu MCU wykonaj aktualizację
hosta przed Home lub grzaniem:

```bash
cd ~/inventure-src &&
git fetch origin --tags &&
git switch --detach inventure-information-r25-rc1 &&
python3 install-new-host.py --repair-home-procedures --serial /dev/ttyS5 --baud 750000 --apply
```

Odczyt po aktualizacji:

```gcode
INVENTURE_VERSION
INVENTURE_INFO
```

Oczekiwane: MCU `RC8-CONTROL-R7-HOME-R25-INFO-OLED`,
`information_release: RC8-INFORMATION-R25.1`, `identity_verified: true` oraz
rzeczywiste `serial_number`, `hardware_version`, `factory_print_minutes`
i `total_printing_time`. Świeży start zwykle ustala potwierdzenia w pierwszym
cyklu odczytu; kontrola wykonuje się co 5 sekund.

Wydanie zawiera `R25_INFORMATION.json`, galerię OLED, dowód SRAM w
`R25_INFO_BUILD.json`, wyniki Home/menu, `SHA256SUMS` i odpowiadający słownik
MCU. Zapisane ustawienia i pliki kalibracji pozostają zachowane przez instalator.
