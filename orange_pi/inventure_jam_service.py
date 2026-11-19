"""Exercise filament recovery on an idle Inventure using the owned factory transport."""
from .inventure_factory_core import FactoryError, _snapshot, delta32


def run(program, operation):
    p = program.p
    cassette = operation == 'JAM_CASSETTE'
    yield from program.home(y_first=True)
    yield from program.park()
    yield from program.heat()
    selection = yield from program.select_tool()
    if not selection['a']:
        raise FactoryError('Insert filament into the selected path before recovery')
    # All E-only strokes start over the bin, with the selected nozzle at temperature.
    yield from program._phase('JAM_RETRACT')
    yield from program._move(*program.xy, e=-1., speed=p['e_speed'])
    if cassette:
        terminal = yield from _cassette(program)
    else:
        yield from program._phase('EXTERNAL_RELOAD')
        for _ in range(4):
            snap = yield from program._move(*program.xy, e=5., speed=p['e_speed'])
            if not snap['a']:
                raise FactoryError('External filament is absent; reload the spool')
        terminal = 'EXTERNAL_CHECK_FLOW'
    yield from program._mode(0)
    if terminal != 'CASSETTE_RELOAD_L4':
        yield from program._phase('JAM_WIPE')
        yield from program.clean()
        yield from program.return_to_bin()
    yield from program._phase('DONE')
    yield dict(kind='done', success=True, terminal=terminal, cleaning_done=True,
               transport_confirmed=cassette, flow_confirmed=False, print_resumed=False)


def _cassette(program):
    speed = program.p['e_speed']
    yield from program._phase('JAM_R1')
    first = yield from program._mode(2)
    result = _snapshot((yield dict(kind='feed_until_edges', mm=50., speed=speed,
                                   count=10, quiet_s=2., pulse_base=first['pulses'])))
    yield from program._mode(0)
    if (not result.get('ok') or delta32(result['pulses'], first['pulses']) < 10
            or result['pulse_age_ms'] > 2000):
        raise FactoryError('Extruder jam: R1 did not confirm ten recent transport edges')
    yield from program._phase('JAM_R2')
    second = yield from program._mode(1)
    result = _snapshot((yield dict(kind='feed_until_starts', mm=50., speed=speed,
                                   count=3, starts_base=second['starts'])))
    selection = yield from program._mode(0)
    if result.get('ok') and delta32(result['starts'], second['starts']) >= 3:
        return 'CASSETTE_R2'
    yield from program._phase('JAM_CASSETTE_RELOAD')
    stage = yield from program.load(selection)
    return 'CASSETTE_RELOAD_' + stage
