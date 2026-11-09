# SPDX-License-Identifier: GPL-3.0-or-later
# Verbatim classes from the project pinned Klipper mcu.py.
# Full source SHA256: fbac5f09566e12244a1bb21a558c2aef1257a3eace57c9465c70ea0e60fcc841

class CommandQueryWrapper:
    def __init__(self, conn_helper, msgformat, respformat, oid=None,
                 cmd_queue=None, is_async=False):
        self._serial = serial = conn_helper.get_serial()
        self._cmd = serial.get_msgparser().lookup_command(msgformat)
        serial.get_msgparser().lookup_command(respformat)
        self._response = respformat.split()[0]
        self._oid = oid
        self._error = conn_helper.get_mcu().get_printer().command_error
        self._xmit_helper = serialhdl.SerialRetryCommand
        if conn_helper.get_mcu().is_fileoutput():
            self._xmit_helper = DummyResponse
        elif is_async:
            self._xmit_helper = RetryAsyncCommand
        if cmd_queue is None:
            cmd_queue = serial.get_default_command_queue()
        self._cmd_queue = cmd_queue
    def _do_send(self, cmds, minclock, reqclock, retry):
        xh = self._xmit_helper(self._serial, self._response, self._oid)
        reqclock = max(minclock, reqclock)
        try:
            return xh.get_response(cmds, self._cmd_queue, minclock, reqclock,
                                   retry)
        except serialhdl.error as e:
            raise self._error(str(e))
    def send(self, data=(), minclock=0, reqclock=0, retry=True):
        return self._do_send([self._cmd.encode(data)], minclock, reqclock,
                             retry)
    def send_with_preface(self, preface_cmd, preface_data=(), data=(),
                          minclock=0, reqclock=0, retry=True):
        cmds = [preface_cmd._cmd.encode(preface_data), self._cmd.encode(data)]
        return self._do_send(cmds, minclock, reqclock, retry)

class AsyncResponseWrapper:
    def __init__(self, conn_helper, cfg_helper, callback, msgformat, oid=None):
        self._serial = conn_helper.get_serial()
        self._callback = callback
        self._msgformat = msgformat
        self._name = msgformat.split()[0]
        self._oid = oid
        if cfg_helper.is_config_finalized():
            self._register()
        else:
            self._serial.register_response((lambda p: None), self._name, oid)
            cfg_helper.register_post_init_callback(self._register)
    def _register(self):
        self._serial.get_msgparser().lookup_command(self._msgformat)
        self._serial.register_response(self._callback, self._name, self._oid)
    def unregister(self):
        self._serial.register_response(None, self._name, self._oid)
