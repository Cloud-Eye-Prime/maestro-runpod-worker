"""Offline tests for scene_builder.select_cycles_device -- the one pure
function in scene_builder.py that does not need a real Blender process.
bpy is only importable inside Blender, so a minimal stub is installed
into sys.modules before import; select_cycles_device itself never
touches bpy directly, only the fake prefs object passed to it."""
import sys
import types

if "bpy" not in sys.modules:
    _bpy_stub = types.ModuleType("bpy")
    _bpy_stub.types = types.SimpleNamespace(
        RenderSettings=types.SimpleNamespace(
            bl_rna=types.SimpleNamespace(properties={"engine": types.SimpleNamespace(enum_items=[])})))
    _bpy_stub.context = types.SimpleNamespace()
    _bpy_stub.data = types.SimpleNamespace()
    _bpy_stub.ops = types.SimpleNamespace()
    sys.modules["bpy"] = _bpy_stub

import scene_builder  # noqa: E402


class _FakeDevice:
    def __init__(self, dev_type, name):
        self.type = dev_type
        self.name = name
        self.use = False


class _FakePrefs:
    """Mimics bpy.context.preferences.addons["cycles"].preferences enough
    for select_cycles_device: compute_device_type gates which types
    get_devices() will populate into .devices, matching the real API's
    behaviour that prefs.devices only lists devices for the currently
    selected backend (plus CPU, irrelevant here)."""
    def __init__(self, available_types, rejects=()):
        self.compute_device_type = None
        self._available_types = set(available_types)
        self._rejects = set(rejects)
        self.devices = []

    def get_devices(self):
        if self.compute_device_type in self._available_types:
            self.devices = [_FakeDevice(self.compute_device_type, "fake-gpu-0")]
        else:
            self.devices = []


class _RejectingPrefs(_FakePrefs):
    def __setattr__(self, name, value):
        if name == "compute_device_type" and getattr(self, "_rejects", None) and value in self._rejects:
            raise TypeError("%s is not a valid compute_device_type" % value)
        super().__setattr__(name, value)


def test_selects_optix_when_listed():
    prefs = _FakePrefs(available_types=["OPTIX"])
    device, reason = scene_builder.select_cycles_device(prefs)
    assert device == "OPTIX"
    assert prefs.devices[0].use is True
    assert "OPTIX" in reason


def test_falls_back_to_cuda_when_optix_not_listed():
    prefs = _FakePrefs(available_types=["CUDA"])
    device, reason = scene_builder.select_cycles_device(prefs)
    assert device == "CUDA"
    assert prefs.devices[0].use is True


def test_falls_back_to_cpu_when_none_listed():
    prefs = _FakePrefs(available_types=[])
    device, reason = scene_builder.select_cycles_device(prefs)
    assert device == "CPU"
    assert "OPTIX" in reason and "CUDA" in reason


def test_rejected_compute_device_type_is_recorded_not_raised():
    prefs = _RejectingPrefs(available_types=["CUDA"], rejects={"OPTIX"})
    device, reason = scene_builder.select_cycles_device(prefs)
    assert device == "CUDA"
