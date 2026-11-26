"""M300-style Home presentation around the accepted Inventure owner.

No motion, heat, interlock, coordinate or ownership policy is implemented here.
Observers call each original bound method exactly once with unchanged arguments.
"""
from functools import wraps
import logging
import textwrap

UI_RELEASE = 'RC8-HOME-MENU-R24.4'
HOME_ACTIONS = {'home_' + axes.lower(): axes for axes in ('X', 'Y', 'Z', 'XY', 'XYZ')}


class HomePresentation:
    def __init__(self, native, menu):
        self.native, self.menu = native, menu
        self.busy = False
        self.generation = 0
        self.axes = self.message = ''
        self._observe('_run_home_axes', self._run)
        self._observe('_home_limit', self._limit)
        self._observe('_operator_clearance', self._clearance)
        self._observe('_stage', self._stage)

    def _observe(self, name, observer):
        original = getattr(self.native, name)
        @wraps(original)
        def wrapped(*args, **kwargs):
            return observer(original, *args, **kwargs)
        setattr(self.native, name, wrapped)

    def _safe(self, function, *args):
        # A failed display must neither prevent nor alter a Home operation.
        try:
            return function(*args)
        except Exception:
            logging.exception('Inventure Home presentation unavailable')

    def _phase(self, message):
        if self.busy and message != self.message:
            self.message = message
            self.generation += 1
            self.menu._wake()

    def _begin(self, axes):
        self.axes, self.busy = axes, True
        self.message = ''
        self._phase('Preparing homing')

    def _finish(self, success, reason=''):
        self.busy = False
        self.generation += 1
        if not success:
            view = self.menu._info('Homing failed', reason)
            view.icon = 'activity:error'
        self.menu._wake()

    def _run(self, original, gcmd, operator):
        if self.native.sequence_running:
            return original(gcmd, operator)
        self._safe(self._begin, gcmd.get('AXES', '').upper())
        try:
            result = original(gcmd, operator)
        except BaseException as exc:
            self._safe(self._finish, False, str(exc))
            raise
        else:
            self._safe(self._finish, True)
            return result

    def _limit(self, original, axis, *args, **kwargs):
        self._safe(self._phase, 'Homing ' + axis)
        result = original(axis, *args, **kwargs)
        if 'Z' in self.axes:
            self._safe(self._phase, 'Heating nozzle for Z')
        return result

    def _clearance(self, original, *args, **kwargs):
        self._safe(self._phase, 'Lowering platform before XY')
        return original(*args, **kwargs)

    def _stage(self, original, name, *args, **kwargs):
        low = name.lower()
        message = ('Refining Z reference' if 'refin' in low else
                   'Moving to Z clearance' if 'clearance' in low else
                   'Homing Z' if any(s in low for s in ('coarse', 'approach', 'contact')) else
                   'Checking Z contact' if low == 'preflight' else
                   'Verifying Z reference' if 'commit' in low else None)
        if message:
            self._safe(self._phase, message)
        return original(name, *args, **kwargs)

    def render(self):
        if not self.busy:
            return None
        # Read the existing interpreter's cached phase; never poll a wire.
        context = getattr(self.menu.board, 'load_test_context', None) or {}
        factory = context.get('factory') or {}
        stack = factory.get('module_stack') or ()
        module = stack[-1] if stack else None
        if isinstance(module, dict):
            module = module.get('name')
        message = {'heat': 'Heating nozzle for Z', 'select_tool': 'Selecting nozzle T0',
                   'clean': 'Cleaning nozzle T0', 'z_position_pad': 'Moving to Z contact',
                   'home_z': 'Lowering platform'}.get(module, self.message)
        labels = ['Home ' + self.axes] + textwrap.wrap(message, 24) + ['Please wait']
        return dict(page='home_progress', generation=(self.generation, message),
                    selected=0, window=0, labels=labels, notice=None,
                    icon='home:home_' + self.axes.lower(), animated=True)


class HomeMenuUI:
    """Keep Home menu, macros and G28 on the same existing native owner."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.home_ui = None

    def _attach_home_ui(self):
        native = self._obj('inventure_native_z')
        if native is None or not hasattr(native, '_operator_clearance'):
            return
        ui = getattr(native, '_home_presentation', None)
        if ui is None:
            ui = native._home_presentation = HomePresentation(native, self)
        ui.menu = self
        self.home_ui = ui

    def _ready(self):
        super()._ready()
        self._attach_home_ui()

    def _tick(self, eventtime):
        if self.home_ui is not None and self.home_ui.busy:
            # T0 preparation belongs to Home, not to a second menu procedure.
            return eventtime + .25
        return super()._tick(eventtime)

    def _shutdown(self, *args):
        if self.home_ui is not None:
            self.home_ui.busy = False
        return super()._shutdown(*args)

    def open(self, action, **params):
        if action not in HOME_ACTIONS:
            return super().open(action, **params)
        self._attach_home_ui()
        # Retain the original lifecycle, active-owner, fan and capability checks.
        view = super().open(action, **params)
        if view is None or view.kind != 'confirm' or view.data.get('action') != action:
            return view
        # The selected Home is the start action, as in M300. Consume its fresh
        # bound choice without publishing a second Yes/Start web or OLED view.
        view.claim(view.token, self.reactor.monotonic(), self._binding())
        return_web_page = self.operator_page_open
        self._close_menu_dialog()
        self.menu_dialog_requested = self.operator_page_open = False
        self.view = None
        was_executing, self.executing = self.executing, True
        try:
            self._execute(action, view.data.get('parameters', {}))
        finally:
            self.executing = was_executing
        if return_web_page:
            return self.open_operator_page()

    def _execute(self, action, params):
        if action in HOME_ACTIONS:
            axes = HOME_ACTIONS[action]
            if params:
                # Internal parameterized callers keep the original owner API.
                super()._execute(action, params)
            else:
                name = 'ALL' if axes == 'XYZ' else axes
                self.gcode.run_script_from_command('INVENTURE_HOME_' + name)
                self.last = dict(action=action, result='completed')
            # The procedure borrows the display, then returns to the same row.
            self.view = None
            self._wake()
            return None
        return super()._execute(action, params)

    def view_status(self):
        if self.home_ui is not None and self.home_ui.busy:
            return self.home_ui.render()
        return super().view_status()

    def get_status(self, now):
        result = super().get_status(now)
        result['home_ui_release'] = UI_RELEASE
        result['home_progress'] = self.home_ui.render() if self.home_ui is not None else None
        return result
