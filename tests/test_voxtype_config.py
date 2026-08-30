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
            # 7e41fe8 translates the raw gate rejection into an actionable
            # message so the panel can offer the explicit ONNX setup action.
            with self.assertRaisesRegex(
                voxtype_config.EngineUnavailable, "standard Whisper binary"
            ):
                voxtype_config.set_engine("sensevoice")

    def test_set_engine_passes_through_unrelated_rejection(self) -> None:
        rejected = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="unknown config key"
        )
        with mock.patch.object(voxtype_config.subprocess, "run", return_value=rejected):
            with self.assertRaisesRegex(RuntimeError, "unknown config key"):
                voxtype_config.set_engine("whisper")

    def test_variants_without_onnx_install_reject_before_download(self) -> None:
        variants = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=(
                "Variants\n"
                "  Active: Whisper (native)\n"
                "  Available:\n"
                "    ONNX (native) not installed\n"
            ),
            stderr="",
        )
        with mock.patch.object(voxtype_config.subprocess, "run", return_value=variants):
            with self.assertRaisesRegex(voxtype_config.EngineUnavailable, "No ONNX Voxtype variant"):
                voxtype_config.check_engine_feature("sensevoice")

    def test_installed_onnx_variant_passes_variant_probe(self) -> None:
        variants = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="Available:\n  ONNX (native) installed\n",
            stderr="",
        )
        with mock.patch.object(voxtype_config.subprocess, "run", return_value=variants):
            voxtype_config.check_engine_feature("sensevoice")

    def test_arm_does_not_use_packaged_binary_switch(self) -> None:
        with mock.patch.object(voxtype_config.platform, "machine", return_value="aarch64"):
            self.assertFalse(voxtype_config.automatic_onnx_setup_supported())
            self.assertTrue(voxtype_config.onnx_install_supported())

    def test_arm_uses_verified_onnx_binary_for_cli_mutations(self) -> None:
        self.models.mkdir()
        arm_binary = self.models / "voxtype"
        arm_binary.write_bytes(b"verified arm binary")
        with (
            mock.patch.object(voxtype_config.platform, "machine", return_value="aarch64"),
            mock.patch.object(voxtype_config, "ARM_ONNX_INSTALL", arm_binary),
            mock.patch.object(voxtype_config, "ARM_ONNX_SHA256", voxtype_config.sha256_file(arm_binary)),
        ):
            self.assertEqual(voxtype_config.voxtype_command(), str(arm_binary))

    def test_arm_install_passes_verified_bytes_to_fixed_privileged_helper(self) -> None:
        payload = b"verified arm binary"
        target = self.models / "installed" / "voxtype"
        override = self.models / "systemd" / "10-arm-onnx.conf"
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.read.side_effect = [payload, b""]
        successful = subprocess.CompletedProcess([], 0, stdout=b"", stderr=b"")
        with (
            mock.patch.object(voxtype_config.platform, "machine", return_value="aarch64"),
            mock.patch.object(voxtype_config, "ARM_ONNX_INSTALL", target),
            mock.patch.object(voxtype_config, "ARM_ONNX_SERVICE_OVERRIDE", override),
            mock.patch.object(voxtype_config, "ARM_ONNX_SHA256", voxtype_config.hashlib.sha256(payload).hexdigest()),
            mock.patch.object(voxtype_config.urllib.request, "urlopen", return_value=response),
            mock.patch.object(voxtype_config.shutil, "which", return_value="/usr/bin/pkexec"),
            mock.patch.object(voxtype_config.subprocess, "run", return_value=successful) as run,
            mock.patch.object(voxtype_config, "restart_daemon"),
        ):
            voxtype_config.install_arm_onnx()

        install_call = run.call_args_list[0]
        self.assertEqual(install_call.args[0][:3], ["pkexec", voxtype_config.sys.executable, "-c"])
        self.assertEqual(install_call.kwargs["input"], payload)
        self.assertNotIn(str(target), install_call.args[0])

    def test_non_arm_keeps_path_binary_for_cli_mutations(self) -> None:
        with (
            mock.patch.object(voxtype_config.platform, "machine", return_value="x86_64"),
            mock.patch.object(voxtype_config.shutil, "which", return_value="/usr/bin/voxtype"),
        ):
            self.assertEqual(voxtype_config.voxtype_command(), "/usr/bin/voxtype")

    def test_restart_daemon_surfaces_systemd_error(self) -> None:
        rejected = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="restart failed"
        )
        with mock.patch.object(voxtype_config.subprocess, "run", return_value=rejected):
            with self.assertRaisesRegex(RuntimeError, "restart failed"):
                voxtype_config.restart_daemon()

    def test_model_switch_rejects_missing_engine_readback(self) -> None:
        with (
            mock.patch.object(voxtype_config, "check_engine_feature"),
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
            mock.patch.object(voxtype_config, "check_engine_feature"),
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

    def test_model_files_present_rejects_corrupt_file(self) -> None:
        spec = {
            "engine": "sensevoice",
            "model": "test",
            "directory": "test-model",
            "source": "https://example.invalid/",
            "files": [("model.bin", "0" * 64, 4)],
        }
        model_dir = self.models / "test-model"
        model_dir.mkdir(parents=True)
        (model_dir / "model.bin").write_bytes(b"good")
        with mock.patch.object(voxtype_config, "SASAYAKI_MODELS", {"test": spec}):
            self.assertFalse(voxtype_config.model_files_present("test"))

    def test_model_files_present_rejects_symlink(self) -> None:
        spec = {
            "engine": "sensevoice",
            "model": "test",
            "directory": "test-model",
            "source": "https://example.invalid/",
            "files": [("model.bin", "0" * 64, 4)],
        }
        model_dir = self.models / "test-model"
        model_dir.mkdir(parents=True)
        outside = self.models / "outside.bin"
        outside.write_bytes(b"good")
        (model_dir / "model.bin").symlink_to(outside)
        with mock.patch.object(voxtype_config, "SASAYAKI_MODELS", {"test": spec}):
            self.assertFalse(voxtype_config.model_files_present("test"))

    def test_model_download_rejects_response_over_declared_size(self) -> None:
        class OversizedResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, size: int) -> bytes:
                return b"12345"[:size]

        spec = {
            "engine": "sensevoice",
            "model": "test",
            "directory": "test-model",
            "source": "https://example.invalid/",
            "files": [("model.bin", "0000000000000000000000000000000000000000000000000000000000000000", 4)],
        }
        with (
            mock.patch.object(voxtype_config, "SASAYAKI_MODELS", {"test": spec}),
            mock.patch.object(voxtype_config.urllib.request, "urlopen", return_value=OversizedResponse()),
            self.assertRaisesRegex(RuntimeError, "exceeds declared size"),
        ):
            voxtype_config.ensure_model("test")

    def test_ensure_voxtype_binary_skips_install_when_present(self) -> None:
        with (
            mock.patch.object(voxtype_config.shutil, "which", return_value="/usr/bin/voxtype"),
            mock.patch.object(voxtype_config.subprocess, "run") as run,
        ):
            voxtype_config.ensure_voxtype_binary()
        run.assert_not_called()

    def test_ensure_voxtype_binary_installs_via_pkexec_when_missing(self) -> None:
        installed = subprocess.CompletedProcess([], 0)
        with (
            mock.patch.object(
                voxtype_config.shutil,
                "which",
                side_effect=[None, "/usr/bin/pkexec", "/usr/bin/pacman", "/usr/bin/voxtype"],
            ),
            mock.patch.object(voxtype_config.subprocess, "run", return_value=installed) as run,
        ):
            voxtype_config.ensure_voxtype_binary()
        self.assertEqual(
            run.call_args.args[0],
            ["pkexec", "pacman", "-S", "--noconfirm", "--needed", "voxtype-bin"],
        )

    def test_ensure_voxtype_binary_surfaces_cancelled_install(self) -> None:
        cancelled = subprocess.CompletedProcess([], 1, stdout="", stderr="Dismissed")
        with (
            mock.patch.object(
                voxtype_config.shutil,
                "which",
                side_effect=[None, "/usr/bin/pkexec", "/usr/bin/pacman"],
            ),
            mock.patch.object(voxtype_config.subprocess, "run", return_value=cancelled),
        ):
            with self.assertRaisesRegex(RuntimeError, "Dismissed"):
                voxtype_config.ensure_voxtype_binary()

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
