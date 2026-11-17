# Kontrola MCU R10 i hosta R14

Audyt obejmuje `RC8-CONTROL-R7-PAD-THERMAL-R10` i
`RC8-CONTROL-R7-UIFAST-PAD-XYZ-R14` z commita
`dc2c4046a658280c79fe0f29cb52bc1861125fda`. Poprawki wykonawcy procedur
wchodzą do hosta `RC8-CONTROL-R7-UIFAST-PAD-XYZ-R15`, współpracującego
z tą samą aplikacją MCU R10. Data kontroli to 17 listopada 2026.

R14 przeszedł ponownie 519 testów hosta i sześć zestawów testów C MCU.
Kontrola trajektorii wykazała jednak dwa błędy parkowania, których
dotychczasowe testy zakończenia procedur nie sprawdzały. Pełny fizyczny
odbiór wszystkich wymienionych procedur pozostaje otwarty. Dostępne
próby potwierdzają bazowanie i wybrane operacje obu torów, a końcowe
wyniki pełnej kalibracji przy 100°C wymagają próby na drukarce.

| Procedura | Sprawdzenie programowe | Dostępne potwierdzenie z drukarki |
|---|---|---|
| Homing X i Y | Najazd, zwolnienie krańcówki, kierunki, osobne osie i XY, brak grzania | Wcześniejsze X/Y i odjazdy potwierdzone. Złożone makra w bieżącej wersji wymagają próby |
| Homing Z do PE12 | Limit drogi, polaryzacja, brak dodatkowych kroków przy aktywnym wejściu | Dolna krańcówka i zatrzymanie potwierdzone wcześniej |
| Robocze Z przez PC14 | Ochrona tacki, pomiar MCU, 101 cykli bez poprawy, przekazanie pozycji i unieważnienie po utracie napędu | Wcześniejszy ręczny pomiar natywny zakończył się Z=0. Złożony XYZ wymaga próby |
| Krótkie ładowanie T0 | L1, impulsy transportu, starty kasety, budżet E, kierunek DIR0 | Potwierdzono wypływ i czyszczenie. Brakuje identyfikatora L1 dla tego fizycznego przebiegu |
| Długie ładowanie T0 | L2 → L3 → L4, A, sześć impulsów, limit drogi, końcowe czyszczenie i parkowanie | Potwierdzono wcześniejszy pełny transport kasety i E. Osobna kwalifikacja gałęzi AUTO L4 pozostaje otwarta |
| Krótkie ładowanie T1 | L1, prawa kaseta, impulsy PD14, E DIR1 | Potwierdzono wypływ i czyszczenie. Brakuje identyfikatora L1 |
| Długie ładowanie T1 | L2 → L3 → L4, własne A i impulsy, przeciwna polaryzacja E | Brakuje osobnego zapisu zakończenia AUTO L4 |
| Wyładowanie T0 | U0–U3, limity U1/U2, cisza transportu, świeże A=0, kierunek E DIR1 | Potwierdzono wcześniej cofanie E. Pełny cykl U0–U3 wymaga potwierdzenia |
| Wyładowanie T1 | U0–U3, prawa kaseta, E DIR0, brak sukcesu przy A=1 | Pełny cykl U0–U3 wymaga potwierdzenia |
| Czyszczenie T0 i T1 | Wspólna geometria szczotki, wybór dyszy, zakończenie nad koszem w R15 | Czyszczenie obu dysz było obserwowane. Nowe zakończenie samodzielnego CLEAN/PURGE wymaga próby |
| Zimny środek pada T0 | Cztery krawędzie, limit Z, ponowienie po drganiu PC14 | Środek X81,7 Y68,2 mm, limit 139,50 mm |
| Zimny środek pada T1 | Własny selektor, limit Z i cztery krawędzie | Środek X70,8 Y68,7 mm, limit 137,95 mm |
| Pełny pad przy 100°C | T0 → T1 → T0, SWEEP/TAP, przedział krawędzi do 0,05 mm, powtarzalność XY/Z | Brakuje zakończonego przebiegu przy 100°C i zapisanego offsetu z drukarki |
| Offset XYZ i SAVE | Znak przesunięcia, średnia obu T0, odrzucenie niepowtarzalności, identyfikator przebiegu i zapis atomowy | Wymaga pełnego wyniku poprzedniego wiersza |

Szczegóły prób fizycznych pochodzą z zapisów projektu i pięciu przesłanych
archiwów `klippy(7)`–`klippy(11)`. Archiwa częściowo powtarzają wcześniejsze
sesje. Ostatnie zapisy pada dotyczą celu 160°C i kończą się błędami,
w tym `Unverified Z motion result` oraz przedziałem krawędzi 0,16 mm.
Występujący w tych logach host `...PAD-XYZ-R10` jest wcześniejszą wersją
hosta. Kontrolowane tutaj MCU ma pełną nazwę `...PAD-THERMAL-R10`.
Zestawienie zachowuje sumy plików źródłowych, numery wierszy i wyniki.
[Wyniki z logów](evidence/INVENTURE_R10_R14_FIELD_RESULTS_2026-11-17.json).

Wcześniejszy fizyczny test CASSETTE1 dla T0 potwierdził 171800 kroków E,
183 impulsy transportu PB12, około 150 s podawania i wypływ potwierdzony
przez operatora. Osobne potwierdzenie wypływu i wycierania obu dysz
dotyczy wydania T1-TIMING. Nie zapisano wtedy osobnych identyfikatorów
gałęzi obu przebiegów. Ręczny natywny pomiar Z z 14 listopada zakończył
się po 102 cyklach, w tym 101 bez poprawy, przy 139,369077 mm od PE12.
[CASSETTE1](evidence/CASSETTE1_PHYSICAL_LOAD_2026-11-10.json),
[próby obu torów](FACTORY_PROCEDURE_AUDIT_2026-11-12.md),
[natywne Z](NATIVE_Z_COREXY.md).

1. **Zakończenie nad szczotką w R14.** Samodzielne CLEAN i PURGE z
   czyszczeniem kończyły nad szczotką dla obu torów. Długa ścieżka AUTO
   T1 w profilu SERVICE miała ten sam problem. W R15 głowica najpierw
   odsuwa się w Y przy końcowym X szczotki, a potem wraca na środek kosza.
   Wykonawca kończy i zwalnia właściciela dopiero po potwierdzonym ruchu.
   Pełna kalibracja pada korzysta z tej samej funkcji powrotu.

2. **Nagrzewanie przed dojazdem nad kosz w R14.** CLEAN, PURGE, MANUAL
   i część procedur T1 rozpoczynały oczekiwanie na temperaturę przy 0,0.
   W R15 wszystkie grzane procedury kasetowe po bazowaniu dojeżdżają
   nad kosz przed pierwszym włączeniem grzałki. Dotychczasowe trajektorie
   parkowania OEM i kolejność bazowania osi są zachowane w swoich
   wywołaniach. Profile dopuszczające bezpośredni dojazd do środka
   korzystają z tej istniejącej geometrii.

3. **Niepełny główny zestaw kontroli MCU.** Testy
   `test_load15_joint.c` i `test_cassette_load_joint.c` istniały poza listą
   uruchamianą przez CI. Oba przeszły na aktualnych źródłach. Skrypt
   uruchamia teraz osiem zestawów C, obejmujących wspólne grzanie,
   E, kasetę, STOP, utratę heartbeat i wygaśnięcie wyjść.

4. **Kontrola protokołu bieżącego obrazu.** Dotychczasowy test zgodności
   używał słownika starszego R7-FACTORY-FEED. Dodany test uruchamia pełną
   konfigurację hosta i natywnego Z wobec słownika z dostarczonego MCU R10.
   Sprawdza wszystkie żądane formaty poleceń i odpowiedzi, oba bity narzędzi,
   limit temperatury 310°C, pasmo pada 95–105°C i timeout heartbeat 5 s.

Przykłady odtworzonego błędu parkowania R14 są podane w układzie SERVICE.
PURGE w tej tabeli obejmuje domyślne końcowe czyszczenie.

| Operacja i profil | Pozycja końcowa R14 | Pozycja końcowa R15 |
|---|---|---|
| CLEAN/PURGE T0 SERVICE lub OEM | X105,8 Y154,5 | X82,8 Y152,5 |
| CLEAN/PURGE T1 SERVICE | X44,5 Y152,0 | X67,5 Y150,0 |
| CLEAN/PURGE T1 OEM | X49,0 Y154,5 | X72,0 Y152,5 |
| AUTO L4 T1 SERVICE | X90,5 Y152,0 | X67,5 Y150,0 |

Sprawdzenie R15 obejmuje 522 testy hosta oraz osiem uruchamianych
programów C MCU. Dwie nowe macierze kontrolują po 24 warianty procedur
na poziomie planera i wykonawcy protokołu. Obejmują T0/T1, SERVICE/OEM,
krótkie i długie AUTO, CLEAN, PURGE, MANUAL i UNLOAD. Kontrolują pozycję
przed pierwszym włączeniem grzałki oraz po zakończeniu. Testy utrzymują
dotychczasowe sprawdzenia kroków E, kierunków, selektora i decyzji L1–L4.
Testy są uruchamiane ponownie przez GitHub Actions przed publikacją.

| Kontrola | Wynik i zakres |
|---|---|
| Słownik rzeczywistego MCU R10 i host | Zgodne formaty poleceń i odpowiedzi, również ochrona i refinacja natywnego Z |
| Heartbeat | Aktywne grzanie przy 4,999 s i odcięcie przy 5 s w modelach SERVICE/NATIVE, wraz z chłodzeniem |
| Temperatura | Obie dysze przyjmują cel do 310°C. Odczyt 310°C uruchamia limit MCU |
| Pad | Obie dysze muszą mieć 95–105°C przy uzbrojeniu i podczas ruchu pomiarowego |
| Drzwi | Wspólne ustawienie użytkownika, domyślnie wyłączone, sprawdzane także podczas sesji |
| Ruch i przerwanie | Limity drogi, token właściciela, potwierdzone liczniki, brak późniejszego START po STOP |
| Instalacja | Kopie aktywnych modułów i konfiguracji, ponowne uruchomienie instalatora, przywrócenie po błędzie |

Homing ma dwa obsługiwane wejścia. SERVICE `INVENTURE_PROCEDURE ACTION=HOME`
ustala X/Y i dolną referencję PE12. Natywny CoreXY udostępnia
`INVENTURE_NATIVE_HOME AXES=X`, `Y`, `XY`, `Z` i `XYZ`.
Makra `INVENTURE_HOME_*` są aliasami z pliku konfiguracji.
Kontaktowy pomiar roboczego Z korzysta z T0, temperatury domyślnej 200°C
i ochrony PC14/PC13. Po zmianie właściciela lub wyłączeniu napędu poprzednia
referencja jest unieważniana. [Wejścia homingu i parametry](NATIVE_Z_COREXY.md).

AUTO wybiera krótką albo długą ścieżkę na podstawie transportu.
Udany L1 kończy się `factory.result.terminal=L1`, a długa ścieżka
`terminal=L4`. U0–U3 wymaga świeżego potwierdzenia A=0, aby zgłosić
zakończone wyładowanie. Polecenia stałoodcinkowe LOAD i RETRACT są
zachowane i sprawdzane osobno. W tych poleceniach operator ustawia
mechanicznie właściwą dyszę. Wspólny automat PROCEDURE wykonuje własny
docisk selektora. [Ładowanie](FACTORY_LOAD_2026-11-10.md),
[wycofanie odcinka](RETRACT_2026-11-10.md),
[kierunki T1](T1_LOAD_2026-11-10.md).

Pełny pomiar pada wyznacza fizyczny offset T1 w XY, przeciwny do niego
ruch karetki, różnicę wysokości i powtarzalność ponownego osadzenia T0.
Średnia z dwóch pomiarów T0 stanowi referencję. Zimne środki podane przez
użytkownika dają różnicę karetki X−10,9 Y+0,5 mm. Pełny pomiar przy 100°C
dostarcza własny wynik i poziomy Z. Przedział 0,05 mm jest warunkiem
zawężenia krawędzi w próbach TAP, używanych także do potwierdzania SWEEP.
Rzeczywista powtarzalność wymaga pomiaru na drukarce.
[Algorytm i zapis wyniku](NOZZLE_PAD_CALIBRATION.md).

`ACTION=SAVE` zapisuje zaakceptowany wynik do `inventure_nozzle_pad.json`.
Aktualny kod używa tego pliku jako trwałego zapisu pomiaru. Automatyczne
stosowanie jego XYZ w zmianie narzędzia podczas wydruku pozostaje
do wdrożenia. Testy samego pomiaru i zapisu przechodzą. Oddzielna kalibracja
wydrukiem zapisuje `inventure_nozzle_xy.json`, odczytywany przez procedury
XY i wyznaczanie punktu startowego T1. [Zapis pada](../orange_pi/inventure_mcu.py),
[kalibracja wydrukiem](NOZZLE_XY_ALIGNMENT_R0323.md).

Po aktualizacji hosta do R15 można wykonać odbiór w poniższej kolejności.
Wszystkie czynności SERVICE zaczynają się od `INVENTURE_CONTROL MODE=SERVICE`.
Temperatury i materiał wybiera się odpowiednio dla rzeczywiście załadowanych
filamentów. Testów fizycznych poniżej audyt nie wykonywał zdalnie.

1. Odczytać `INVENTURE_VERSION`, sprawdzić R15/R10, `heartbeat_timeout_s: 5`,
   `baud_match: true` oraz oba natywne limity 310°C.
2. Sprawdzić HOME, osobny SELECT T0 i T1 oraz zimny SWEEP.
3. Sprawdzić samodzielne CLEAN dla T0 i T1 oraz końcowy postój nad koszem.
4. Wykonać po jednym krótkim i długim AUTO dla każdego toru. Zachować
   końcowy STATUS z narzędziem, wynikiem L1/L4 i potwierdzeniem wypływu.
5. Wykonać UNLOAD U0–U3 dla obu torów i zachować zakończenie z A=0.
6. Wykonać pełny pad T0 → T1 → T0 przy 100°C. Po `candidate` zachować
   pomiary, rozrzut i offset XYZ, a następnie zapisać wybrany przebieg.

Dokładna komenda pełnego pomiaru z pustymi dyszami i sprawdzonymi limitami
tego egzemplarza jest podana poniżej.

```gcode
INVENTURE_CONTROL MODE=SERVICE
ZORTRAX_NOZZLE_PAD_CALIBRATE ACTION=START MODE=FULL SCAN=SWEEP PREP=EMPTY ACK=PAD_GEOMETRY_MEASURED T0_PAD_X=81.70 T0_PAD_Y=68.20 T1_PAD_X=70.80 T1_PAD_Y=68.70 T0_Z_MAX=139.50 T1_Z_MAX=137.95 PROCEDURE_TIMEOUT=3600
```

Przy filamentach w dyszach używa się `PREP=RETRACT` z osobnymi
temperaturami i długościami wycofania. Głowica po czyszczeniu wraca nad
kosz i tam czeka na schłodzenie do pomiaru.
