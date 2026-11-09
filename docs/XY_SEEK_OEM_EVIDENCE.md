# Jednorazowy najazd XY — MOTOR-R3

Podstawa: projektowy audyt `ZORTRAX_INVENTURE_r0323_RUCH_I_MIKRORUCH_KLIPPER_2026-10-04.md`,
rozdział 11; sprawdzone ponownie bajty `application_r0323.bin`, SHA256
`9b980ba04531fac4425b77a23969076d24d8f25629fc2a501ac05d262c142268`.

Deskryptory X `0x08036CD4` i Y `0x08036CF0` wskazują limit 0x19000,
czyli 102400 jednostek krokowych. Przy efektywnej skali OEM 640 kroków/mm
daje to 160 mm. To limit procedury, nie zmierzony rozmiar roboczy.

| Tor | Callback dojazdu OEM | DIR PB2 / PB1 | Wejście |
|---|---|---|---|
| X | `0x08013F24` (adres Thumb 0x08013F25) | 0/0 przy zwykłym znaku X | PB11, aktywne=1 |
| Y | `0x08013F74` (adres Thumb 0x08013F75) | 0/1 | PC15, aktywne=1 |

OEM bit systemowy 14 odwraca znak X: 0x08013F24 ustawia obie linie DIR
na HIGH, gdy bit 14 jest ustawiony, i na LOW, gdy jest wyzerowany.
Callback 0x08013F44 realizuje przeciwny kierunek X. Y ma stałą parę DIR
0/1 w dojeździe i 1/0 w zwalnianiu (0x08013F64).

Samo warunkowe rozgałęzienie w plannerze 0x0801F2D0 nie oznacza wyłączenia
transformacji CoreXY: obie gałęzie przechodzą do sumy/różnicy osi, różni się
znak składowej X. MOTOR-R3 nie wczytuje ani nie zapisuje fabrycznego NVM;
stosuje jawnie raportowane maski X=0, Y=2 zgodne ze zwykłym znakiem OEM.
Fizyczny kierunek i trafienie na drukarce należy teraz potwierdzić.

R3 wykonuje jedną fazę do kontaktu. Nie przenosi fabrycznego zwalniania,
drugiego najazdu, przestawienia współrzędnych ani zapisów kalibracyjnych.
Prędkość do 3200 par impulsów/s (nominalne 5 mm/s) jest limitem próby,
nie deklaracją fabrycznej prędkości bazowania. Impuls 100 us pozostaje
zgodny ze sprawdzonym testem napędów MOTOR-R1/R2.

Potwierdzenie użytkownika przed R3: A porusza głowicą z widoczną składową X;
B z widoczną składową Y; robocze zero ma być z przodu po lewej.
Nie traktujemy tych małych ruchów jako pomiaru skali ani dowodu pełnej geometrii.
R3 zatrzymuje ruch w MCU na kontakcie i nie ustawia flagi homed w Klipperze.

Nowe uruchomienie przy już aktywnym docelowym wejściu nie wykonuje ruchu
i raportuje `endstop_already_active`; nie jest to świeżo potwierdzony najazd.
