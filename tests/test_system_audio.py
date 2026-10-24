from __future__ import annotations

from reezyn import system_audio
from reezyn.system_audio import DeviceWatcher, clamp_volume, output_volume, set_output_volume


def test_device_watcher_reports_each_switch_once():
    readings = [(1, 2), (1, 2), (3, 2), (3, 2), (3, 4)]
    inputs: list[int] = []
    outputs: list[int] = []
    watcher = DeviceWatcher(
        inputs.append,
        outputs.append,
        reader=lambda: readings.pop(0),
    )

    for _ in range(5):
        watcher.poll_once()

    assert inputs == [3]
    assert outputs == [4]


def test_device_watcher_keeps_the_other_device_when_one_changes():
    readings = [(1, 2), (3, 2), (3, 4)]
    inputs: list[int] = []
    outputs: list[int] = []
    watcher = DeviceWatcher(
        inputs.append,
        outputs.append,
        reader=lambda: readings.pop(0),
    )
    watcher.poll_once()
    watcher.poll_once()
    watcher.poll_once()
    assert inputs == [3]
    assert outputs == [4]


def test_device_watcher_survives_a_failed_read():
    calls = {"count": 0}

    def reader():
        calls["count"] += 1
        if calls["count"] == 1:
            raise OSError("CoreAudio went away")
        return (1, 2)

    watcher = DeviceWatcher(lambda _d: None, lambda _d: None, reader=reader)
    assert watcher.poll_once() == (False, False)
    assert watcher.poll_once() == (False, False)


def test_volume_is_clamped_to_the_system_range():
    assert clamp_volume(-10) == 0
    assert clamp_volume(0) == 0
    assert clamp_volume(45) == 45
    assert clamp_volume(140) == 100


def test_output_volume_parses_the_script_result(monkeypatch):
    monkeypatch.setattr(system_audio, "_osascript", lambda _script: "72\n")
    assert output_volume() == 72

    monkeypatch.setattr(system_audio, "_osascript", lambda _script: "loud")
    assert output_volume() is None

    monkeypatch.setattr(system_audio, "_osascript", lambda _script: None)
    assert output_volume() is None


def test_set_output_volume_clamps_and_writes_osascript(monkeypatch):
    scripts: list[str] = []

    def fake(_script: str) -> str:
        scripts.append(_script)
        return ""

    monkeypatch.setattr(system_audio, "_osascript", fake)
    assert set_output_volume(150) is True
    assert scripts == ["set volume output volume 100"]
