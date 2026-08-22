from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPT = Path(__file__).parents[1] / "scripts" / "voxtype-config.py"
SPEC = importlib.util.spec_from_file_location("voxtype_config", SCRIPT)
assert SPEC and SPEC.loader
voxtype_config = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(voxtype_config)


class VoxtypeConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        root = Path(self.temp_dir.name)
        self.config = root / "config.toml"
        self.models = root / "models"
        self.config.write_text(
            'engine = "whisper"\n\n[whisper]\nmodel = "base.en"\nlanguage = "zh"\n',
            encoding="utf-8",
        )
        self.config_patch = mock.patch.object(voxtype_config, "CONFIG", self.config)
        self.models_patch = mock.patch.object(voxtype_config, "MODELS_DIR", self.models)
        self.config_patch.start()
        self.models_patch.start()
        self.addCleanup(self.config_patch.stop)
        self.addCleanup(self.models_patch.stop)

    def test_set_engine_surfaces_voxtype_feature_gate_error(self) -> None:
        rejected = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="engine 'sensevoice' is not compiled into this binary"
        )
        with mock.patch.object(voxtype_config.subprocess, "run", return_value=rejected):
            with self.assertRaisesRegex(RuntimeError, "not compiled into this binary"):
                voxtype_config.set_engine("sensevoice")

    def test_restart_daemon_surfaces_systemd_error(self) -> None:
        rejected = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="restart failed"
        )
        with mock.patch.object(voxtype_config.subprocess, "run", return_value=rejected):
            with self.assertRaisesRegex(RuntimeError, "restart failed"):
                voxtype_config.restart_daemon()

    def test_model_switch_rejects_missing_engine_readback(self) -> None:
        with (
            mock.patch.object(voxtype_config, "ensure_model"),
            mock.patch.object(voxtype_config, "set_engine"),
            mock.patch.object(voxtype_config, "restart_daemon") as restart,
        ):
            with self.assertRaisesRegex(RuntimeError, "did not retain"):
                voxtype_config.set_setting("model", "sensevoice-int8")
        restart.assert_not_called()

    def test_model_switch_verifies_then_restarts(self) -> None:
        def persist_engine(engine: str) -> None:
            text = voxtype_config.set_value(voxtype_config.read_text(), "", "engine", engine)
            self.config.write_text(text, encoding="utf-8")

        with (
            mock.patch.object(voxtype_config, "ensure_model"),
            mock.patch.object(voxtype_config, "set_engine", side_effect=persist_engine),
            mock.patch.object(voxtype_config, "model_files_present", return_value=True),
            mock.patch.object(voxtype_config, "restart_daemon") as restart,
        ):
            voxtype_config.set_setting("model", "sensevoice-int8")
            snapshot = voxtype_config.config_snapshot()
            self.assertEqual(snapshot["engine"], "sensevoice")
            self.assertEqual(snapshot["model"], "small-int8")
            self.assertEqual(snapshot["model_id"], "sensevoice-int8")
        restart.assert_called_once_with()

    def test_downloaded_model_is_not_active_under_whisper(self) -> None:
        with mock.patch.object(voxtype_config, "model_files_present", return_value=True):
            snapshot = voxtype_config.config_snapshot()
        self.assertEqual(snapshot["engine"], "whisper")
        self.assertEqual(snapshot["model_id"], "")

    def test_main_emits_structured_json_for_runtime_error(self) -> None:
        output = io.StringIO()
        with (
            mock.patch.object(sys, "argv", [str(SCRIPT), "set", "model", "sensevoice-int8"]),
            mock.patch.object(voxtype_config, "set_setting", side_effect=RuntimeError("feature unavailable")),
            contextlib.redirect_stdout(output),
            self.assertRaises(SystemExit) as exit_error,
        ):
            voxtype_config.main()
        self.assertEqual(exit_error.exception.code, 1)
        self.assertEqual(json.loads(output.getvalue()), {"error": "feature unavailable"})


if __name__ == "__main__":
    unittest.main()
