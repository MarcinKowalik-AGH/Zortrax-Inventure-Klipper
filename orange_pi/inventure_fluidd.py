"""Keep existing G-code scripts compatible with the English Fluidd buttons."""

LEGACY_COMMANDS = {
    'INV_D_WERSJE': 'INV_D_VERSIONS',
    'INV_D_WEJSCIA': 'INV_D_INPUTS',
    'INV_D_RFID_KASETY': 'INV_D_RFID_CARTRIDGES',
    'INV_D_OPERACJE': 'INV_D_OPERATIONS',
    'INV_D_ZASOBY': 'INV_D_RESOURCES',
    'INV_O_TEST_TEKST': 'INV_O_TEXT_TEST',
    'INV_M_MATERIALY': 'INV_M_MATERIALS',
    'INV_M_KATALOG': 'INV_M_CATALOG',
    'INV_M_ZUZYCIE': 'INV_M_CONSUMPTION',
    'INV_C_KALIBRACJA': 'INV_C_CALIBRATION',
    'INV_C_POZIOMOWANIE': 'INV_C_LEVELING',
    'INV_C_WYROWNANIE': 'INV_C_ALIGNMENT',
    'INV_P_CYKL': 'INV_P_CYCLE',
    'INV_P_WERYFIKUJ': 'INV_P_CHECK',
    'INV_F_WENTYLATORY': 'INV_F_FANS',
    'INV_T_TERMIKA': 'INV_T_THERMAL',
    'INV_R_REFERENCJE': 'INV_R_REFERENCES',
    'INV_R_WYBIERZ_T0': 'INV_R_SELECT_T0',
    'INV_R_WYBIERZ_T1': 'INV_R_SELECT_T1',
    'INV_C_POZIOMUJ_START': 'INV_C_LEVEL_START',
    'INV_C_POZIOMUJ_DALEJ': 'INV_C_LEVEL_NEXT',
    'INV_C_POZIOMUJ_ANULUJ': 'INV_C_LEVEL_CANCEL',
    'INV_C_ALIGN_ANULUJ': 'INV_C_ALIGN_CANCEL',
}


def register_legacy_commands(printer, gcode):
    for old, current in LEGACY_COMMANDS.items():
        macro = printer.lookup_object('gcode_macro ' + current, None)
        if macro is not None and old not in gcode.ready_gcode_handlers:
            gcode.register_command(old, macro.cmd)
