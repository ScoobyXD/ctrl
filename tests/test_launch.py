"""A Stop request invalidates startup before and after PID publication."""

import sys
from unittest.mock import Mock

import pytest

from ctrl import launch


def configure(monkeypatch, tmp_path, generation):
    root = tmp_path / "app"
    (root / ".ctrl").mkdir(parents=True)
    monkeypatch.setattr(launch, "__file__", str(root / "ctrl" / "launch.py"))
    monkeypatch.setattr(sys, "argv", ["ctrl.launch", "--stop-generation", generation])
    return root


def test_stop_before_launcher_prevents_process_creation(monkeypatch, tmp_path):
    root = configure(monkeypatch, tmp_path, "old-start")
    (root / ".ctrl" / "stop-generation").write_text("new-stop")
    spawn = Mock()
    monkeypatch.setattr(launch.subprocess, "Popen", spawn)
    with pytest.raises(SystemExit, match="cancelled"):
        launch.main()
    spawn.assert_not_called()


def test_stop_during_creation_is_detected_and_server_inherits_old_generation(monkeypatch, tmp_path):
    root = configure(monkeypatch, tmp_path, "initial")

    def spawn(*_args, **kwargs):
        assert kwargs["env"]["CTRL_START_GENERATION"] == "initial"
        (root / ".ctrl" / "stop-generation").write_text("stop-during-spawn")
        return Mock(pid=731)

    monkeypatch.setattr(launch.subprocess, "Popen", spawn)
    with pytest.raises(SystemExit, match="cancelled"):
        launch.main()
    assert not (root / ".ctrl" / "server.pid").exists()
    assert launch.stop_generation(root) == "stop-during-spawn"


def test_explicit_new_start_can_follow_a_stop(monkeypatch, tmp_path, capsys):
    root = configure(monkeypatch, tmp_path, "latest-stop")
    (root / ".ctrl" / "stop-generation").write_text("latest-stop")
    spawn = Mock(return_value=Mock(pid=732))
    monkeypatch.setattr(launch.subprocess, "Popen", spawn)
    launch.main()
    assert capsys.readouterr().out.strip() == "732"
    assert spawn.call_args.kwargs["env"]["CTRL_START_GENERATION"] == "latest-stop"
