"""Shared OLED/Fluidd artwork; existing owners retain every actuator command."""
MENU_AUDIT_RELEASE = 'RC8-OPERATOR-MENU-R24.5'


def oem(address):
    return 'oem:0x%08X' % address


ACTION_ICONS = {
    'load_cartridge_t0': oem(0x0802D058), 'load_cartridge_t1': oem(0x0802D068),
    'unload_t0': oem(0x0802D3D4), 'unload_t1': oem(0x0802D4B0),
    'manual_load_t0': oem(0x0802D20C), 'manual_load_t1': oem(0x0802D2E4),
    'manual_unload_t0': oem(0x0802D3D4), 'manual_unload_t1': oem(0x0802D4B0),
    'select_t0': 'project:heat_nozzle', 'select_t1': 'project:heat_nozzle',
    'maintenance_t0': 'project:heat_nozzle', 'maintenance_t1': 'project:heat_nozzle',
    'heat': 'project:heat_nozzle', 'heat_t0': 'project:heat_nozzle', 'heat_t1': 'project:heat_nozzle',
    'clean_t0': 'project:clean_nozzle', 'clean_t1': 'project:clean_nozzle',
    'purge_t0': 'project:clean_nozzle', 'purge_t1': 'project:clean_nozzle',
    'alignment': 'maintenance', 'leveling': 'leveling:0x0802A218',
    'move_platform': 'home:home_z', 'platform_up': 'home:home_z', 'platform_down': 'home:home_z',
    'fan_test': 'project:fan_upper', 'fans_info': 'project:fan_upper',
    'light': 'project:led_brightness', 'oled_sleep_light': 'project:led_brightness',
    'oled_sleep_mode': 'activity:sleep', 'oled_icons': 'project:oled_brightness',
    'oled_brightness': 'project:oled_brightness', 'oled_dim': 'project:oled_brightness',
    'oled_sleep': 'project:oled_brightness', 'oled_timers': 'project:oled_brightness',
    'oled_idle': 'project:oled_brightness', 'oled_notice': 'project:oled_brightness',
    'spool_catalog_model': 'spool', 'spool_catalog_support': 'spool', 'spool_load': 'spool',
    'ignore_filament': 'spool', 'save_print': 'filecheck',
    'pause': oem(0x0802DE00), 'resume': oem(0x0802DE00), 'cancel_yes': oem(0x0802DE00),
    'cancel_no': oem(0x0802DE00), 'job_info': oem(0x0802DE00),
    'filecheck_on': 'filecheck', 'filecheck_off': 'filecheck',
    'language_en': 'language', 'language_pl': 'language',
    'sort_desc': 'sorting', 'sort_asc': 'sorting', 'sort_name': 'sorting', 'sort_size': 'sorting',
    'door_lock': 'doorlock', 'door_unlock': 'doorlock',
    'version': 'information', 'printer_info': 'information', 'help': 'information',
    'current_procedure': 'maintenance', 'go_status': 'project:project-klipper',
    'thermal_info': 'project:heat_nozzle', 'mcu_info': 'project:link_status',
    'uart_info': 'project:link_status', 'adc_info': 'information', 'rfid_info': 'information',
    'oled_info': 'project:oled_brightness', 'settings_info': 'settings',
    'sd_browser': 'project:sd_read', 'heaters_off': 'project:cooling',
    'filament_jam': 'project:filament_jam',
}
CHILD_ICONS = {
    'launcher': 'project:project-klipper', 'root': 'project:project-zortrax',
    'models': 'models', 'maintenance': 'maintenance', 'heads': 'maintenance',
    'material': 'material', 'model_material': 'material', 'support_material': 'material',
    'load_model': oem(0x0802D20C), 'load_support': oem(0x0802D2E4),
    'spool_model': oem(0x0802D3C4), 'spool_support': oem(0x0802D590),
    'settings': 'settings', 'language': 'language', 'sorting': 'sorting',
    'doorlock': 'doorlock', 'filecheck': 'filecheck', 'information': 'information',
    'pause': oem(0x0802DE00), 'stop_confirm': oem(0x0802DE00),
    'pause_material': 'maintenance', 'cancel': oem(0x0802DE00), 'question': 'information',
    'klipper_main': 'project:project-klipper', 'klipper_control': 'home:home_xyz',
    'klipper_settings': 'settings', 'klipper_information': 'information',
    'service_tests': 'project:link_status',
}


def action_icon(action):
    if action and action.startswith('home_'):
        return 'home:' + action
    return ACTION_ICONS.get(action)


def animated(icon):
    return bool(icon and icon.startswith(('project:', 'home:')))


class MenuPresentation:
    """Decorate views without modifying choices, tokens, parameters or owners."""
    def open(self, action, **params):
        previous = getattr(self, '_art_action', None)
        self._art_action = action
        try:
            return super().open(action, **params)
        finally:
            self._art_action = previous

    def _choice_icon(self, choice, fallback):
        if choice is None:
            return fallback
        verb, args = choice
        if verb in ('request', 'execute', 'display_edit'):
            return action_icon(args.get('action')) or fallback
        if verb == 'operator_item':
            nav = self._obj('inventure_oled').oem_menu
            return nav.item_icon(nav.items()[args['index']]) or fallback
        if verb == 'operator_page':
            return CHILD_ICONS.get(args.get('page'), fallback)
        if verb == 'fan_page':
            name = self.board.FAN_NAMES[args['channel']]
            return 'project:fan_lower' if 'model' in name or 'bottom' in name else 'project:fan_upper'
        if verb in ('set_light', 'display_save'):
            return 'project:led_brightness' if verb == 'set_light' else 'project:oled_brightness'
        if verb in ('browse', 'preview'):
            return 'project:sd_read'
        if verb in ('jam_command', 'jam_report', 'jam_test', 'jam_source_menu', 'exchange_steps'):
            return 'project:filament_jam'
        return fallback

    def _fan_page(self, channel):
        previous = getattr(self, '_art_action', None)
        self._art_action = 'fan_test'
        try:
            return super()._fan_page(channel)
        finally:
            self._art_action = previous

    def _set_view(self, kind, title, options, **kwargs):
        data = kwargs.get('data') or {}
        action = data.get('action') or getattr(self, '_art_action', None)
        active = getattr(self, 'active', None) or {}
        if action is None and kind == 'running':
            action = active.get('action')
            context = active.get('object')
            if action == 'external_service' and isinstance(context, dict):
                operation = (context.get('factory') or {}).get('operation')
                prefix = {'AUTO': 'load_cartridge', 'UNLOAD': 'unload', 'CLEAN': 'clean',
                          'PURGE': 'purge', 'SELECT': 'select'}.get(operation)
                if prefix and context.get('tool') in (0, 1):
                    action = prefix + '_t%d' % context['tool']
        supplied = kwargs.get('icon')
        if kind == 'info' and title in ('Unavailable', 'Operation unavailable', 'Homing failed', 'Procedure interrupted'):
            supplied = 'activity:error'
        if supplied == 'activity:oled_brightness':
            supplied = 'project:oled_brightness'
        icon = supplied or action_icon(action) or ('maintenance' if kind == 'running' else 'information')
        kwargs['icon'] = icon
        kwargs['animated'] = kwargs.get('animated', False) or animated(icon)
        view = super()._set_view(kind, title, options, **kwargs)
        if kind in ('choice', 'fan', 'files', 'preview', 'job') and not data.get('leveling'):
            view.option_icons = tuple(self._choice_icon(choice, icon) for _, choice in view.options)
        return view

    def get_status(self, now):
        result = super().get_status(now)
        result['menu_audit_release'] = MENU_AUDIT_RELEASE
        return result

    def view_status(self):
        result = super().view_status()
        view = getattr(self, 'view', None)
        if result is not None and view is not None and view.data.get('operator_page'):
            nav = self._obj('inventure_oled').oem_menu
            if nav.page == view.data['operator_page']:
                result['window'] = nav.window
        return result

    def panel_move(self, direction):
        view = getattr(self, 'view', None)
        if view is not None and view.data.get('operator_page'):
            nav = self._obj('inventure_oled').oem_menu
            nav.move(direction)
            return self.open_operator_page()
        return super().panel_move(direction)


class OLEDMenuPresentation:
    def _home_busy(self):
        if super()._home_busy():
            return True
        menu = getattr(getattr(self, 'board', None), 'zortrax', None)
        # Optional pixels only: keep the existing thermal hold, query schedule,
        # ACK window, UART budget and every actuator owner unchanged.
        return bool(menu is not None and not menu.stopped and menu.active is not None)
