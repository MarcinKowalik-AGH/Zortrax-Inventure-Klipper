"""Exact M300 Information page roles over Inventure's cached data owner."""
class InformationMenu:
    @staticmethod
    def _information_lines(action, info):
        if action == 'printer_info':
            hardware = info['hardware_version']
            return ['Model: '+info['printer_model'],
                    'Hardware version: '+(str(hardware) if hardware is not None else 'Unavailable'),
                    'Serial number: '+(info['serial_number'] or 'Unavailable'),
                    'Total printing time: '+info['total_printing_time']]
        release = str(info['mcu_release'] or 'Unavailable')
        label = 'R25-INFO-OLED' if 'R25-INFO' in release else 'R24-OLED' if 'HOME-R24' in release else release
        return ['Host R38.6 / Info R25.2', 'MCU '+label,
                'Klipper ee2c6fb6b00b', 'OEM reference 1.6.1 r0323']

    def _refresh_information(self):
        view = getattr(self, 'view', None)
        if view is None or view.data.get('action') not in ('printer_info', 'version'):
            return
        owner = self._obj('inventure_information')
        if owner is None:
            return
        info = owner.get_status(self.reactor.monotonic())
        options = (view.options[0],)+tuple((line,None) for line in self._information_lines(view.data['action'],info))
        if options != view.options:
            # Informational text alone changed. Back, choices, selection,
            # token, deadline, display activity and its sleeping state stay put.
            view.options = options
            view.data['information_refresh_pending'] = True
        view.data['information'] = info

    def _flush_information_prompt(self):
        if getattr(getattr(self, 'home_ui', None), 'busy', False):
            return
        view = getattr(self, 'view', None)
        if view is not None and view.data.pop('information_refresh_pending',False) and self.menu_dialog_requested:
            self._close_menu_dialog()
            self._sync_menu_dialog()

    def view_status(self):
        self._refresh_information()
        return super().view_status()

    def _tick(self, now):
        self._refresh_information()
        due = super()._tick(now)
        self._flush_information_prompt()
        return due

    def _information_view(self, kind, title, options, **kwargs):
        previous = getattr(self, 'view', None)
        action = kwargs.get('data', {}).get('action')
        selected = (previous.data.get('information_parent_selection', 0)
                    if previous is not None and previous.data.get('information_parent') == action else 0)
        view = self._set_view(kind, title, options, **kwargs)
        view.selected = min(selected, len(view.options)-1)
        return view

    def panel_back(self):
        view = getattr(self, 'view', None)
        home = getattr(self, 'home_ui', None)
        if (view is not None and view.data.get('information_parent') and self.active is None
                and self.fan_session is None and not (home is not None and home.busy)):
            return self.open(view.data['information_parent'])
        return super().panel_back()

    def _read(self, action):
        if action == 'information_errors' or action.startswith('information_error_'):
            from .inventure_information_errors import CODES
            if action == 'information_errors':
                options=([('Back', ('request', dict(action='help')))]+
                    [(row['title'], ('request', dict(action='information_error_%d' % i))) for i,row in enumerate(CODES)])
                return self._information_view('choice', 'Errors', options,
                    data=dict(action=action, information_parent='help', information_parent_selection=1), icon='activity:error')
            index=int(action.rsplit('_',1)[1])
            row=CODES[index]
            lines=([('OEM code #%d' % row['code'])] if row['code'] else [])+[row['hint1'], row['hint2']]
            return self._information_view('info',row['title'],
                [('Back',('request',dict(action='information_errors')))]+[(line,None) for line in lines if line],
                data=dict(action=action,information_parent='information_errors',information_parent_selection=index+1),icon='activity:error')
        if action not in ('version', 'printer_info', 'help'):
            return super()._read(action)
        home = getattr(self, 'home_ui', None)
        oled = self._obj('inventure_oled')
        if oled is not None and self.active is None and self.fan_session is None and not (home is not None and home.busy):
            nav = oled.oem_menu
            selected = nav.items()[nav.selected] if nav.items() else None
            if not self.operator_page_open or selected is None or selected.action != action:
                nav.open_page('information')
                nav.selected = next(i for i,item in enumerate(nav.items()) if item.action == action)
                nav.window = 1
                self.operator_page_open = self.menu_dialog_requested = True
                oled.oem_display_mode = 'menu'
        owner = self._obj('inventure_information')
        if owner is None:
            return self._info('Information', 'Inventure information service unavailable')
        info = owner.get_status(self.reactor.monotonic())
        if action == 'version':
            title = 'Firmware version'
            lines = self._information_lines(action, info)
        elif action == 'printer_info':
            title = 'Printer information'
            lines = self._information_lines(action, info)
        else:
            title = 'Help'
            lines = ['github.com/', 'MarcinKowalik-AGH/', 'Zortrax-Inventure-Klipper',
                     'Project documentation and issues']
        options=[('Back', ('close', {}))]
        if action == 'help':
            options.append(('Errors', ('request', dict(action='information_errors'))))
        return self._information_view('info', title,
            options+[(line, None) for line in lines],
            data=dict(action=action, information=info), icon='information')
