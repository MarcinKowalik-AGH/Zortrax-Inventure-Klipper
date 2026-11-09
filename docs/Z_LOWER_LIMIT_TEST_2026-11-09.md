# Fizyczna próba Z i dolnej krańcówki — 2026-11-09

Host RC8-CONTROL-R5-ZLIMIT; MCU RC8-CONTROL-R5; InventureBoot 1.7; UART 750000.

| Sprawdzenie | Wynik |
|---|---|
| DIR=1 | użytkownik potwierdził podnoszenie stołu |
| DIR=0 | użytkownik potwierdził opuszczanie stołu |
| PE12 po odejściu od dołu | surowy 0 |
| PE12 przy powrocie na dół | surowy 1, aktywny HIGH |
| Zatrzymanie podczas ruchu w dół | 1457 z planowanych 1604 impulsów; 1,852 s; reason=stop_input, hit=4, hit_inputs=[z_bottom] |
| Pozostałe napędy podczas tej próby | A/B/E: po 0 impulsów |
| Dwie nowe próby dalszego opuszczania na aktywnej krańcówce | każda 0 impulsów Z; reason=stop_input, hit=4 |

Wcześniejszy ruch w górę zakończył się po 1604 impulsach w 2,046 s. Pierwszy
powrót o 1604 impulsy zakończył się liczbą kroków; dopiero następny odcinek
trafił na PE12 po 1457 impulsach. Nie wyciągamy z tego pomiaru całego zakresu Z.

Potwierdzono obie strony ruchu, dwa poziomy PE12, zatrzymanie przez dolny
czujnik i brak dalszych impulsów po jego zadziałaniu. To kwalifikacja dolnej
krańcówki i sterowania napędem. Kontakt dyszy PC14, geometria, skala mm,
homing przy platformie i cały wydruk pozostają osobnymi próbami.

Ten wynik nie wymaga zmiany MCU ani aktualizacji kodu hosta.
