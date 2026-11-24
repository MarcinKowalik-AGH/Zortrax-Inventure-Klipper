# Inventure R19: pojedynczy planer normalnego ruchu (zasada M300)

## Cel i architektura

R19 realizuje wyłącznie punkt 1: jeden planner Klippera dla normalnej pracy
(X/Y/Z home, G0/G1, druk, transformacja Bed Mesh). Operator Home Z / Home All
przekazuje napędy SERVICE wyłącznie na ściśle wydzielony etap fizycznego
wyboru T0, czyszczenia i dojazdu na pad. Nie ma już drugiego bazowania XY
wykonywanego przez osobny SERVICE DDA w normalnej procedurze.

## Jedyna sekwencja Home XYZ

1. NATIVE: Klipper G28 Z kieruje stół w dół do PE12, przed przejazdem głowicy
2. NATIVE: Klipper G28 Y i G28 X, raz, przez własny planer i endstopy
3. WERYFIKACJA: zakończone ruchy, obie osie homed; XY równe własnemu
   position_endstop, PE12 zamknięty, PC14 otwarty; silniki opróżnione
4. EXCLUSIVE SERVICE: zasilanie i kolejki natywne wyłączone, procedura wykonuje
   WYŁĄCZNIE bin → grzanie → selektor T0 → wipe → bin → pad.
   Nie wykonuje samodzielnego Home XY ani wstępnego ruchu Z
5. POTWIERDZENIE: MCU kończy wszystkie ruchy i zwraca certyfikowany
   native_xy_offsets_mm=[0,0] i faktyczne XY pada
6. NATIVE: Klipper odzyskuje pozycję XY pojedynczym set_position i wykonuje
   własny G28 Z / natywne PC14 homing/refinement, końcowy odjazd Z

Home Z po wcześniej zbazowanych X/Y nie powtarza G28 Y/X: Klipper jedynie
dojeżdża do własnego XY origin przy stole bezpiecznie opuszczonym do PE12.
Home X, Home Y oraz Home XY nie wywołują SERVICE motion ani selektora.

## G0/G1, druk i Bed Mesh

Natywny toolhead i stepper queues Klippera pozostają jedynym normalnym
planerem. R19 nie dodaje własnego G1, transformacji druku ani drugiej
kolejki stepperów. G-code BED_MESH/LOAD/CLEAR nadal korzysta z wbudowanego
modułu Klippera; OEM pięciopunktowy kreator poziomowania Inventure nadal
jest **osobną procedurą SERVICE** i nie staje się automatycznie siatką
Klippera. Uruchomienie BED_MESH_CALIBRATE wymaga osobno skonfigurowanego
i zweryfikowanego fizycznego sondowania; nie dorabiamy niezweryfikowanego
probe do złotych padów. To ograniczenie sprzętowe, a nie drugi planer.

## Instalacja i rollback

Pełny obraz R38.6 oraz R18 pozostają niezmienione; R19 posiada niezależnie
uwierzytelnione nakładki SHA1 dla trzech modułów klippy/extras:
inventure_native_z.py, inventure_z_home.py, inventure_mcu.py.
Nowy host lub istniejący R38.6/R18 przechodzi przez instalator,
który sprawdza dokładne bajty i tworzy kopię; zmienia tylko trzy
moduły, manifest oraz brakujący dawny OLED, jeśli to konieczne.
Instalator nie flashuje MCU; zachowuje istniejący R18.

## Walidacja

Testy obejmują normalne X, Y, XY bez SERVICE; Y/X dokładnie raz w XYZ,
Z-only bez ponownego XY homing, kontakt/park Z, wybór T0 i wipe,
brak publikacji pozycji po odrzuconym przekazaniu, integralność źródeł
oraz rollback. Testy CI nie dowodzą stabilności sprzętowej. Przed
użyciem produkcyjnym konieczne są próby na fizycznej drukarce.

**Poza zakresem punktu 1:** firmware R18 zgłosiło `Timer too close`
podczas natywnego HOME XY. R19 nie zmienia 20-kHz firmware ISR ani
transportu UART. Issue #3 pozostaje otwarte i blokuje wydanie jako stabilne.
