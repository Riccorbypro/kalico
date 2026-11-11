"""Behavior contracts for the small Kevin compatibility patch set."""

import importlib
from types import SimpleNamespace

import pytest

from klippy import mcu
from klippy.extras import fan
from klippy.gcode import GCodeCommand
from klippy.toolhead import ToolHead


@pytest.mark.parametrize(
    "disabled,connected,attempts,wake,messages",
    [
        pytest.param(True, False, 0, 9e99, 0, id="disabled-never-connects"),
        pytest.param(False, True, 1, 9e99, 1, id="enabled-success-stops-timer"),
        pytest.param(False, False, 1, 12.12, 0, id="enabled-failure-retries"),
    ],
)
def test_reconnect_policy(disabled, connected, attempts, wake, messages):
    board = mcu.MCU.__new__(mcu.MCU)
    board.no_reconnect = disabled
    board._reactor = SimpleNamespace(NEVER=9e99)
    board._name = "cartographer"
    board.reconnect_interval = 2.12
    calls, notifications = [], []
    board.recon_mcu = lambda: calls.append("connect") or connected
    board.gcode = SimpleNamespace(
        respond_info=lambda message, **kw: notifications.append(message)
    )

    assert board.non_critical_recon_event(10.0) == pytest.approx(wake)
    assert len(calls) == attempts
    assert len(notifications) == messages


class HardwareFan:
    def __init__(self, config):
        self.speeds = []

    def set_speed_from_command(self, value):
        self.speeds.append(value)


class CommandSink:
    error = ValueError

    def __init__(self):
        self.scripts = []

    def register_command(self, *args):
        pass

    def run_script_from_command(self, script):
        self.scripts.append(script)

    def respond_info(self, *args, **kwargs):
        pass

    def respond_raw(self, *args, **kwargs):
        pass


def make_fan(monkeypatch, mapping=None):
    sink = CommandSink()
    printer = SimpleNamespace(lookup_object=lambda name: sink)
    config = SimpleNamespace(
        get_printer=lambda: printer,
        getlist=lambda name, **kw: mapping,
    )
    monkeypatch.setattr(fan, "Fan", HardwareFan)
    return fan.PrinterFan(config), sink


@pytest.mark.parametrize(
    "command,params,want",
    [
        pytest.param(
            "M106",
            {"P": "2", "S": "128"},
            "SET_FAN_SPEED FAN=auxiliary SPEED=0.501961",
            id="select-second-fan",
        ),
        pytest.param(
            "M107",
            {"P": "1"},
            "SET_FAN_SPEED FAN=chamber SPEED=0",
            id="stop-first-fan",
        ),
        pytest.param(
            "M106",
            {"P": "1", "S": "510"},
            "SET_FAN_SPEED FAN=chamber SPEED=1.000000",
            id="clamp-selected-fan",
        ),
    ],
)
def test_fan_selector_routes_commands(monkeypatch, command, params, want):
    obj, sink = make_fan(monkeypatch, ["chamber", "auxiliary", "chamber"])
    gcmd = GCodeCommand(sink, command, command, params, None, context=None)
    getattr(obj, "cmd_" + command)(gcmd)
    assert sink.scripts == [want]
    assert obj.fan.speeds == []


@pytest.mark.parametrize(
    "selector", ["0", "4", "bad"], ids=["zero", "past-last", "not-integer"]
)
def test_fan_selector_rejects_invalid_index(monkeypatch, selector):
    obj, sink = make_fan(monkeypatch, ["chamber", "auxiliary", "chamber"])
    with pytest.raises(ValueError):
        obj.cmd_M106(
            GCodeCommand(
                sink, "M106", "M106", {"P": selector}, None, context=None
            )
        )
    assert sink.scripts == []
    assert obj.fan.speeds == []


@pytest.mark.parametrize(
    "mapping,params",
    [(None, {"P": "2", "S": "128"}), (["chamber"], {"S": "128"})],
    ids=["mapping-disabled", "no-selector"],
)
def test_fan_selector_preserves_primary_fan(monkeypatch, mapping, params):
    obj, sink = make_fan(monkeypatch, mapping)
    obj.cmd_M106(GCodeCommand(sink, "M106", "M106", params, None, context=None))
    assert obj.fan.speeds == [pytest.approx(128 / 255)]
    assert sink.scripts == []


def test_reset_accel_restores_configured_limits():
    obj = ToolHead.__new__(ToolHead)
    obj.orig_cfg = {"max_accel": 6000.0}
    obj.max_accel = 1200.0
    obj.square_corner_velocity = 5.0
    obj.min_cruise_ratio = 0.5
    obj.reset_accel()
    assert obj.max_accel == 6000.0
    assert obj.max_accel_to_decel == 3000.0
    assert obj.junction_deviation == pytest.approx(0.00172588984322)


@pytest.mark.parametrize(
    "kind", ["digital_out", "pwm"], ids=["bed-warp-output", "wiper-pwm"]
)
def test_virtual_outputs_have_no_hardware_side_effects(kind):
    module = importlib.import_module("klippy.extras.virtual_pins")
    registrations, events = {}, {}
    sink = SimpleNamespace(register_mux_command=lambda *args, **kw: None)
    pins = SimpleNamespace(
        register_chip=lambda name, chip: registrations.update({name: chip})
    )
    printer = SimpleNamespace(
        lookup_object=lambda name: {
            "pins": pins,
            "gcode": sink,
            "mcu": object(),
        }[name],
        register_event_handler=lambda name, cb: events.update({name: cb}),
    )
    obj = module.load_config(SimpleNamespace(get_printer=lambda: printer))
    pin = obj.setup_pin(kind, {"pin": "test", "pullup": False, "invert": False})
    pin.setup_start_value(0.2, 0.0)
    if kind == "pwm":
        pin.set_pwm(10.0, 0.75)
    else:
        pin.set_digital(10.0, 0.75)
    assert registrations["virtual_pin"] is obj
    assert pin.get_status(10.0)["value"] == 0.75
    assert obj.get_status(10.0)["pins"]["test"]["value"] == 0.75
