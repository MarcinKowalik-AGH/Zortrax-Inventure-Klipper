# Audyt ładowania kaset Inventure T0 i T1

Użytkownik potwierdził wypływ filamentu z obu dysz i wykonanie czyszczenia
po zmianie MCU T1-TIMING. Odczyt `factory.result.terminal` z konkretnej
próby rozróżnia L1 i L4. Fizyczny wynik obejmuje po jednej udanej próbie
na tor. Testy programowe obejmują obie gałęzie dla obu torów.

## Sekwencja OEM

| Etap | Ruch i warunek w r0323 |
|---|---|
| HOME, PARK, HEAT, SWITCH | Referencja dolna Z, zwolnione krańcówki Y i X, dojazd nad kosz, rozgrzanie wybranej dyszy, dojazd i docisk jej selektora, powrót nad kosz. |
| L1, ścieżka krótka | OEM zaczyna L1 również przy nieaktywnym A. Oscylacja X z podawaniem E ma porcję nominalną 20 mm i limit pętli 500 mm. Świeży impuls w ciągu 3 s i co najmniej 6 startów podajnika pozwalają zakończyć w L1 bez osobnego przejazdu wycierającego. |
| L2 i L3, ścieżka długa | Przy braku świeżych impulsów z L1 procedura przechodzi do L2. Podajnik czeka do 5 s na A. Potem głowica jedzie do przedniego punktu właściwego dla toru. L3 oczekuje 6 impulsów transportu w jednej serii z przerwami do 2 s, z limitem E 250 mm. |
| L4 | Powrót nad kosz. Oscylacje X z E przy nominalnych 100 mm, osobne czyszczenie i co najmniej 3 starty podajnika. Brak wymaganych startów może uruchomić wycofanie U0–U3 i zakończenie błędem. |

Fabryczna długość porcji jest progiem pętli. Przy standardowej geometrii
r0323 dopełnia ostatnią oscylację. W L1 MCU wysyła 19 693 kroki E na porcję,
a w L4 96 394. Testy porównują również pozycje obu selektorów, pozycje
przednie L3, przeciwną polaryzację E oraz przebieg wycierania.
Współrzędne są nominalne w układzie od zwolnionych krańcówek.

Prędkości zadane OEM to F8000 dla przejazdów, F3000 dla oscylacji z E i
F250 dla docisku selektora. Szybki odcinek CoreXY lub CoreXY+E wykonuje
MCU ze sprzętowymi impulsami STEP 2 µs, liczeniem A/B/E i rampą, do
125 000 zdarzeń/s. F8000 po przekątnej daje około 120 680 zdarzeń/s.
Ruchy o niższej częstotliwości, w tym czyste E w L3 i cofanie, korzystają
z wolniejszego harmonogramu serwisowego. Wartości prędkości są zadane.
Dokładny przebieg czasowy i efektywna prędkość E na sprzęcie wymagają
pomiaru. Fabryczny kod utrzymuje też zapisany cel drugiej dyszy.
Procedura SERVICE steruje grzałką wybranego toru.

## Materiały i temperatura

Katalog obejmuje 22 nazwane materiały i UNKNOWN. Każdy tor przyjmuje
profile modelowe i podporowe. Materiał wybiera cel temperatury, nastawy
podajnika i limity U1/U2. T0 i T1 używają tej samej logiki L1–L4.
Przykładowe wartości z tabeli kaset to Z-PLA 230°C i U1/U2 30/70 mm,
Z-SUPPORT 235°C i 50/50 mm, Z-SUPPORT-PLUS 240°C i 30/70 mm. Pomiar ma
wspólną skalę 5 mV/°C i osobne wejścia PC0 oraz PF8. Host używa PID z
nastawami z r0323, własnym próbkowaniem i ograniczeniami serwisowymi.

Cel r0323 powyżej 240°C jest widoczny w `oem_material_target_c`.
Bez jawnego, właściwego dla materiału `TARGET` w dopuszczalnym zakresie
`PARAMS` pokazuje `target: null` i `target_requires_override: true`,
a grzany cykl kończy walidację przed ruchem. Zakres pozostaje 180–240°C.

## Poprawki i weryfikacja

- L2 odrzuca A odczytane dopiero po terminie, jak w kolejności sprawdzania r0323.
- L3 zachowuje licznik z chwili uruchomienia podajnika, odświeża odczyt po
  ostatnim kroku i rozdziela serie impulsów przerwane ciszą między odczytami.
- Raport pokazuje `cleaning_done` także po wykonaniu modułu czyszczenia.
  Udany L1 ma `cleaning_done: false` i `cleaning_pending: false`.
- Profil temperatury nie podstawia 230°C za niedostępną nastawę materiału.
  Konsola i menu stosują tę samą walidację.

Do rozpoznania kolejnej fizycznej próby wystarczy
`INVENTURE_PROCEDURE ACTION=STATUS`. `factory_transport_completed`
potwierdza zakończenie cyklu na podstawie transportu kasety. Wypływ z dyszy
jest sprawdzany wzrokowo. R7 używa ręcznie wybranego materiału i włożonej
kasety. RFID i pomiar efektywnego przesuwu nie należą do tej kwalifikacji.

Źródło przebiegu to r0323 `0x0801433C` z rekordami torów
`0x08029A64` i `0x08029AA8`. L2 sprawdza czas w `0x08014642–0x0801466E`,
L3 zachowuje licznik w `0x080146B0–0x080146CC`. Tabele kaset i materiałów
są pod `0x08029B30` i `0x08029DB8`. [Parametry i polecenia](FACTORY_LOAD_2026-11-10.md)
