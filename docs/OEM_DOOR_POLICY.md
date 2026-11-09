# Inventure r0323 — przeniesienie ustawienia Door lock

Źródło: istniejący projektowy audyt
`Inventure_EXTERNAL_DRZWI_WENTYLATORY_TERMISTORY_2026-10-02/RAPORT_PL.md`,
rozdział 4, oraz `evidence/03_door_UI_and_wait.txt`.
Analizowany firmware 1.6.1/r0323, SHA256:
`9b980ba04531fac4425b77a23969076d24d8f25629fc2a501ac05d262c142268`.

| Element OEM | Potwierdzone zachowanie |
|---|---|
| Callback `0x080173E4` | Wywołuje setter `0x0801168C(3, 0)`: włączenie polityki |
| Callback `0x0801740C` | Wywołuje setter `0x0801168C(3, 1)`: wyłączenie polityki |
| Procedura `0x08014F74` | Sprawdza bit 3 przez `0x08011674(3)`; ustawiony bit daje wcześniejszy powrót przed kontrolą czujników i załączeniem zamków |
| Ostrzeżenie `0x08036644` | Po wyłączeniu przypomina o ruchomych częściach |
| Klapa | Wejście PE15, zamknięta=1; maska 1; zamek PA3/TIM5 CH4 |
| Przednie drzwi | Wejście PB10, zamknięte=1; maska 2; zamek PA2/TIM5 CH3 |

Gdy polityka jest włączona, OEM stosuje przyciągnięcie CCR=256 przez
100 ticków i podtrzymanie CCR=60, a zwolnienie ma CCR=0. Audyt nie odtworzył
wszystkich reakcji całego programu na otwarcie podczas wydruku lub błędu.
Nie przypisujemy tej nieustalonej części zachowania do portu.

MOTOR-R1 miał bezwarunkowy wymóg obu zamkniętych wejść podczas testu
napędu, chociaż cewki zamków w aplikacji diagnostycznej pozostają wyłączone.
MOTOR-R2 wprowadza odpowiednik polityki OEM w osobnym stanie RAM MCU:
bit 3=0 wymaga obu zamkniętych wejść, bit 3=1 pomija ten warunek.
Fizyczne wejścia są nadal odczytywane i raportowane bez zmiany.

Komenda `INVENTURE_DOOR_POLICY ENABLE=0/1` zmienia stan MCU i odczytuje
potwierdzenie. Bez parametru wykonuje wyłącznie odczyt. Opcje istniejącego
menu OLED Door lock wywołują te same komendy. Zmiana jest odrzucana podczas
ruchu, próby grzania/chłodzenia, próby wentylatora lub zajętego transportu.
Po MCU restart/shutdown obowiązuje domyślnie ENABLE=1. Nie zapisujemy
ustawień OEM, obszaru bootloadera ani dziennika UART.

W tym wydaniu ustawienie steruje ograniczonym testem A/B/Z/E; native STEP
i globalny ARM pozostają nieaktywne. Nie jest to deklaracja ukończenia
obsługi pełnego wydruku, mechanicznych zamków ani wszystkich blokad OEM.
Zachowano dotychczasowe impulsy, kontrolę krańcówek, limit 5 s,
heartbeat, wzajemne wykluczanie z grzaniem i STOP.

Weryfikacja: symulacja rejestrów rzeczywistego źródła MCU z oboma stanami
polityki i otwartymi osłonami; testy rzeczywistego słownika skompilowanego
firmware, parsera G-code, obsługi błędnych odpowiedzi, menu OLED oraz
aktualizacji aktywnego hosta RC72. Próba na fizycznej drukarce dla MOTOR-R2
pozostaje do wykonania przez użytkownika.
