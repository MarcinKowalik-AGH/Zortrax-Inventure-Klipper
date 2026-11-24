# Inventure R20: punkt 2 - wylaczne przekazanie napedow SERVICE / NATIVE

R19 przywraca natywny planer Klippera dla Home, G0/G1 i druku. R20
rozgranicza okres fizycznego wyboru T0 i czyszczenia przez STM32F207.

Operator Home Z/All:
1. Klipper bazuje X/Y i stół na PE12, opróżnia kolejki i wyłącza napędy.
2. STM32 przejmuje sterowanie wyłącznie w SERVICE, gdy firmware potwierdza
   bezczynne napędy, grzałki i poprawne sygnały bezpieczeństwa.
3. Dopuszczone są wyłącznie krótkie ruchy XY: kosz, wybór T0, wipe i pad.
   Nie wolno wykonać ponownego Home X/Y/Z, ruchu Z, E, feedera ani nowego
   niezadeklarowanego etapu podczas natywnego Home.
4. Host potwierdza ukończony selektor, czyszczenie, kompletność pad move,
   pustą kolejkę ruchu i wynik przesunięcia XY [0,0].
5. MCU otrzymuje jeden owner/run/token. Firmware R18 obsługuje identyczną
   retransmisję tokena idempotentnie. Host sprawdza odpowiedź oraz osobno
   pyta o stan NATIVE, armed=1, fault=0, latched=0.
6. Dopiero podpisany informacją MCU wynik native_handoff pozwala
   adapterowi Home Z przyjąć współrzędne. Przy braku potwierdzenia
   ustawianie pozycji Klippera jest zabronione, a STOP zachowuje guard.

T0 i T1 mogą być fizycznie wybierane i czyszczone w niezależnych
procedurach SERVICE; niezmienione ograniczenia MCU nadal obejmują szybki
DDA, termometry i czas sesji. Automatyczny powrót z wybranym T1 do NATIVE
NIE JEST aktywowany: przedtem trzeba zatwierdzić niezależny offset obu
dysz i układ XY/Z, aby nie wprowadzić fałszywej pozycji. T1 w SERVICE
pozostaje w trybie serwisowym. Nie podniesiono temperatur ani prędkości.

Instalator zapewnia dokładną weryfikację SHA, atomową kopię i przywrócenie
pliku z R19. Nie zmienia MCU R18, OLED, makr poziomowania ani kinematyki.
Wymagana pozostaje weryfikacja na fizycznej drukarce. Błąd firmware
Timer too close (issue #3) jest niezależny i nadal nierozwiązany.
