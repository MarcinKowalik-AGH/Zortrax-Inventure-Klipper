# Inventure — grupy makr Fluidd (RC7.1)

W Fluidd przejdź do ustawień makr/kategorii (Macro Categories) i utwórz poniższe kategorie. Przeciągnij do nich wymienione przyciski. Kategoryzacja jest stanem UI Fluidd zapisanym przez aplikację; **Klipper cfg nie tworzy kategorii w bazie Fluidd samym komentarzem**.

| Kategoria Fluidd | Prefiks makr | Co można sprawdzić |
|---|---|---|
| 01 Inventure — Diagnostyka | `INV_D_` | wersja MCU/BOOT, UART/P4, inputy, RFID UART, resources, planowanie |
| 02 Inventure — OLED/Menu | `INV_O_` | status sesji, transport OLED, menu OEM, plansza testowa i reinit |
| 03 Inventure — Materiały | `INV_M_` | P11/P12/P13/P14 status, katalog, zużycie |
| 04 Inventure — Kalibracja | `INV_C_` | P09/P10/toolframe i offsety |
| 05 Inventure — Druk | `INV_P_` | P15 preflight, P16 interlock, P17 cycle |
| 06 Inventure — Termika / Fan / Ruch | `INV_T_`, `INV_F_`, `INV_R_` | termika i tacho, stan osi (tylko odczyt); TEST w osobnej paczce |
| 07 Inventure — Ruch (TEST) | `INV_R_` | fizyczne homing i wybór narzędzia; **osobny plik opt-in** |

**Tryb diagnostyczny:** `inventure_01_diagnostyka.cfg` zawiera 29 makr. Przetwarzanie makr nie zmienia stanu grzałek, silników i RFID. Statusy modułów opcjonalnych sprawdzają, czy sekcja jest wczytana.

**Tryb procedur (opcjonalny):** `inventure_02_procedury_TEST_OPTIN.cfg` ma 19 makr wykonawczych; nie są one automatycznie instalowane i wymagają kwalifikacji sprzętowej. Nie zastępują natywnych limitów Klippera; w szczególności nie narzucają sztucznego ograniczenia 150% w Fluidd i nie obniżają max_velocity/max_accel. Własność procedur pozostaje przy P02/P03/P04/P09/P10/P13/P17.

**Ważne:** jeśli sekcja modułu nie jest załadowana, makro diagnostyczne daje informację „module not loaded” zamiast próbować wykonać nieznaną komendę. Nie oznacza to sprawdzenia rzeczywistego stanu czujnika na drukarce.
