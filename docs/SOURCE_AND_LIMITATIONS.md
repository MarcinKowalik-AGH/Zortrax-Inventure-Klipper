# Third-party sources and hardware scope

This branch is an experimental restore build for the owner's Inventure V9.
The source is a modified, pinned Klipper snapshot (Klipper GPLv3) with
original project overlays. An original *entire* stock Inventure firmware
image, private Flash/NVM/OTP/UID dumps and decryption material are
explicitly excluded. One comparison test relying on a private OEM APP
image is SKIPPED (not passed) in public CI.

Small UI glyph/icon data were extracted from an OEM image supplied for
repair analysis. Their origin does not confer redistribution rights;
assess rights before distributing this public branch. For publishing
only an independent implementation, replace the glyph and icon assets
with licensed resources and update the pinned hashes/fixtures.

Only the custom APP region beginning at 0x08010000 is built; bootloader,
NVM, OTP and option bytes must remain unchanged. No installer flashes any
physical device. The build and software tests do not constitute physical
qualification for motion/heating/homing.


## Oprogramowanie i pochodzenie zasobów

Projekt wykorzystuje przypiętą bazę Klippera; licencja Klippera GPLv3. Oryginalne bitmapy/fonty UI występujące w nakładkach pochodzą z audytu lokalnego firmware OEM właściciela i są przeznaczone do przywrócenia pracy jego urządzenia. Publiczna obecność tego repo nie jest potwierdzeniem posiadania praw do dalszego rozpowszechniania tych zasobów. Nie umieszczaj w zgłoszeniach ani logach dumpów Flash, numerów seryjnych, UID, materiałów kluczy ani innych danych osobistych.

## Testy w GitHub Actions

Po usunięciu jedynego pełnego obrazu OEM z publicznego snapshotu 1 spośród 567 wcześniejszych testów porównujących cały Flash jest SKIPPED. Weryfikator CI oczekuje dokładnie **566 PASS + 1 SKIPPED**, plus **28/28** w warstwie Zero 3/Fluidd. Nie zastępuje tego wyniku sloganem 595/595 PASS. Testy C i kontrola słownika/ELF są oddzielne.
