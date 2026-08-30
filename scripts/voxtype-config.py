#!/usr/bin/env python3
"""Small, dependency-free Voxtype config bridge for the Quickshell widget."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import subprocess
import sys
import tempfile
import urllib.request
import shutil
from pathlib import Path


CONFIG = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "voxtype" / "config.toml"

SASAYAKI_MODELS = {
    "sensevoice-int8": {
        "engine": "sensevoice", "model": "small-int8",
        "directory": "sensevoice-small-int8",
        "source": "https://huggingface.co/csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17/resolve/main/",
        "files": [
            ("model.int8.onnx", "c71f0ce00bec95b07744e116345e33d8cbbe08cef896382cf907bf4b51a2cd51", 239233841),
            ("tokens.txt", "f449eb28dc567533d7fa59be34e2abca8784f771850c78a47fb731a31429a1dc", 315894),
            ("LICENSE", "221c6df10b0931a5629adad671ea48fb7747e034c414b6d2bfa275bc3dd4ea17", 71),
        ],
    },
    "sensevoice-full": {
        "engine": "sensevoice", "model": "small",
        "directory": "sensevoice-small",
        "source": "https://huggingface.co/csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17/resolve/main/",
        "files": [
            ("model.onnx", "977016bd9c79f9eb343430b5cc305e07ab64d5212dff41b0dcfa1694bee9a8cb", 937617178),
            ("tokens.txt", "f449eb28dc567533d7fa59be34e2abca8784f771850c78a47fb731a31429a1dc", 315894),
            ("LICENSE", "221c6df10b0931a5629adad671ea48fb7747e034c414b6d2bfa275bc3dd4ea17", 71),
        ],
    },
    "paraformer-zh-int8": {
        "engine": "paraformer", "model": "paraformer-zh",
        "directory": "paraformer-zh",
        "source": "https://huggingface.co/csukuangfj/sherpa-onnx-paraformer-zh-2023-09-14/resolve/main/",
        "files": [
            ("model.int8.onnx", "f36a0433bcf096bd6d6f11b80a3ac8bed110bdca632fe0d731df8d1a84475945", 243371218),
            ("tokens.txt", "59aba8873a2ed1e122c25fee421e25f283b63290efbde85c1f01a853d83cb6e6", 75756),
        ],
    },
}

MODELS_DIR = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "voxtype" / "models"
UNIVERSAL_PASTE = Path(__file__).with_name("omarchy-universal-paste.py")
UNIVERSAL_SNAPSHOT = f"{UNIVERSAL_PASTE} snapshot"
UNIVERSAL_PASTE_COMMAND = f"{UNIVERSAL_PASTE} paste"
ONNX_ENGINES = {"parakeet", "moonshine", "sensevoice", "paraformer", "dolphin", "omnilingual", "cohere"}
ARM_ONNX_URL = "https://github.com/peteonrails/voxtype/releases/download/v0.7.5/voxtype-0.7.5-linux-aarch64-onnx"
ARM_ONNX_SHA256 = "360cc6e2ccbce7ea0d7c7cf92f23ebada7a6678f0e92ddaecbea44966d757b63"
ARM_ONNX_MAX_SIZE = 50_000_000
ARM_ONNX_INSTALL = Path("/usr/local/bin/voxtype")
ARM_ONNX_SERVICE_OVERRIDE = Path.home() / ".config/systemd/user/voxtype.service.d/10-arm-onnx.conf"
ARM_PRIVILEGED_INSTALL_HELPER = r'''
import os
import tempfile

target = "/usr/local/bin/voxtype"
maximum = 50_000_000
payload = os.read(0, maximum + 1)
if len(payload) > maximum:
    raise SystemExit("ARM ONNX payload exceeds its declared size")

fd, temporary = tempfile.mkstemp(prefix=".voxtype-arm-onnx.", dir=os.path.dirname(target))
try:
    os.fchmod(fd, 0o755)
    with os.fdopen(fd, "wb") as output:
        output.write(payload)
        output.flush()
        os.fsync(output.fileno())
    os.replace(temporary, target)
except BaseException:
    try:
        os.unlink(temporary)
    except FileNotFoundError:
        pass
    raise
'''


class EngineUnavailable(RuntimeError):
    """The selected engine needs an ONNX Voxtype binary."""


def automatic_onnx_setup_supported() -> bool:
    """Whether the distro's `voxtype setup` can switch the packaged binary."""
    return platform.machine().lower() not in {"aarch64", "arm64"}


def onnx_install_supported() -> bool:
    """Whether this plugin has a verified, platform-specific install path."""
    return automatic_onnx_setup_supported() or platform.machine().lower() in {"aarch64", "arm64"}


def voxtype_command() -> str:
    """Return the binary whose engine matches the active ARM installation.

    The ARM installer deliberately leaves the package-owned `/usr/bin/voxtype`
    untouched and redirects only the user service.  The service override does
    not affect CLI calls from the panel, so use the verified ARM binary for
    feature probes and config mutations as well.  Other platforms retain the
    normal PATH lookup.
    """
    if (
        platform.machine().lower() in {"aarch64", "arm64"}
        and ARM_ONNX_INSTALL.is_file()
        and sha256_file(ARM_ONNX_INSTALL) == ARM_ONNX_SHA256
    ):
        return str(ARM_ONNX_INSTALL)
    return shutil.which("voxtype") or "voxtype"


def read_text() -> str:
    try:
        return CONFIG.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


def value(text: str, section: str, key: str, default: str) -> str:
    current = ""
    pattern = re.compile(r"^\s*" + re.escape(key) + r"\s*=\s*([\"'])(.*?)\1\s*(?:#.*)?$")
    for line in text.splitlines():
        header = re.match(r"^\s*\[([^]]+)\]\s*$", line)
        if header:
            current = header.group(1)
            continue
        if current == section:
            match = pattern.match(line)
            if match:
                return match.group(2)
        if section == "" and current == "":
            match = pattern.match(line)
            if match:
                return match.group(2)
    return default


def remove_value(text: str, section: str, key: str) -> str:
    lines = text.splitlines(keepends=True)
    header = re.compile(r"^\s*\[([^]]+)\]\s*$")
    key_pattern = re.compile(r"^\s*" + re.escape(key) + r"\s*=.*(?:\r?\n)?$")
    current = ""
    kept: list[str] = []
    for line in lines:
        match_header = header.match(line.rstrip("\r\n"))
        if match_header:
            current = match_header.group(1)
        if current == section and key_pattern.match(line):
            continue
        kept.append(line)
    return "".join(kept)


def set_value(text: str, section: str, key: str, new_value: str) -> str:
    lines = text.splitlines(keepends=True)
    header = re.compile(r"^\s*\[([^]]+)\]\s*$")
    key_pattern = re.compile(r"^(\s*)" + re.escape(key) + r"\s*=.*?(\r?\n)?$")
    current = ""
    section_found = False
    section_end = None
    for index, line in enumerate(lines):
        match_header = header.match(line.rstrip("\r\n"))
        if match_header:
            if current == section and section_found and section_end is None:
                section_end = index
            current = match_header.group(1)
            if current == section:
                section_found = True
            continue
        if current == section:
            match_key = key_pattern.match(line)
            if match_key:
                newline = "\n" if line.endswith("\n") else ""
                lines[index] = f'{match_key.group(1)}{key} = "{new_value}"{newline}'
                return "".join(lines)

    if section:
        if section_found:
            insert_at = section_end if section_end is not None else len(lines)
            if insert_at > 0 and not lines[insert_at - 1].endswith("\n"):
                lines[insert_at - 1] += "\n"
            lines.insert(insert_at, f'{key} = "{new_value}"\n')
            return "".join(lines)
        if text and not text.endswith("\n"):
            text += "\n"
        return text + f"\n[{section}]\n{key} = \"{new_value}\"\n"
    if text and not text.endswith("\n"):
        text += "\n"
    return text + f'{key} = "{new_value}"\n'


def restart_daemon() -> None:
    result = subprocess.run(
        ["systemctl", "--user", "restart", "voxtype.service"],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(detail or "Could not restart voxtype.service")


def set_engine(engine: str) -> None:
    """Switch engines across current and older Voxtype CLI versions."""
    result = subprocess.run(
        [voxtype_command(), "config", "set", "engine", engine.lower()],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        if engine.lower() in ONNX_ENGINES and "not compiled" in detail.lower():
            raise EngineUnavailable(
                f"Voxtype is using the standard Whisper binary. Enable ONNX support before selecting {engine}."
            )
        # Voxtype 0.7.2 has no `config set` subcommand.  Keep compatibility
        # with that release by updating the same user config the newer CLI
        # would update.  The feature probe runs before this function.
        if "unexpected argument" not in detail.lower() and "unrecognized subcommand" not in detail.lower():
            raise RuntimeError(detail or f"Voxtype rejected the {engine} engine")
        text = set_value(read_text(), "", "engine", engine.lower())
        CONFIG.parent.mkdir(parents=True, exist_ok=True)
        CONFIG.write_text(text, encoding="utf-8")


def check_engine_feature(engine: str) -> None:
    """Avoid downloading a model when the active binary cannot run it.

    Recent Voxtype builds expose their compiled engines through `info
    variants`.  Older builds may not, so an unavailable/unknown probe is
    deliberately treated as inconclusive and the authoritative config
    mutator remains the final check.
    """
    result = subprocess.run(
        [voxtype_command(), "info", "variants"],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return

    features_line = next(
        (line.strip() for line in result.stdout.splitlines()
         if line.strip().lower().startswith("features:")),
        None,
    )
    if features_line is None:
        # Voxtype 0.7.5 reports compiled variants rather than a `features:`
        # line.  This is the format used by the current Omarchy packages.
        # Treat an explicit list with no installed ONNX variant as a hard
        # gate; otherwise the panel downloads hundreds of megabytes and only
        # then reports the much less useful "could not apply" error.
        variant_lines = [
            line.strip().lower()
            for line in result.stdout.splitlines()
            if line.strip().lower().startswith("onnx (")
        ]
        if variant_lines and not any(
            "not installed" not in line and "installed" in line
            for line in variant_lines
        ):
            architecture = platform.machine()
            raise EngineUnavailable(
                "No ONNX Voxtype variant is installed for this machine "
                f"({architecture}). The selected voice model requires an "
                "ONNX-capable Voxtype build."
            )
        return

    features = {
        feature.strip().lower()
        for feature in features_line.split(":", 1)[1].split(",")
        if feature.strip()
    }
    normalized = engine.lower()
    if normalized in features:
        return

    # Some distro wrappers expose only one default feature in this summary
    # even when the selected binary has additional ONNX engines.  Probe the
    # actual engine with an invalid audio file: a supported engine proceeds to
    # audio parsing, while an unsupported one fails with "not compiled".
    if normalized in ONNX_ENGINES:
        probe = subprocess.run(
            [voxtype_command(), "--engine", normalized, "transcribe", os.devnull],
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
        )
        probe_detail = f"{probe.stdout}\n{probe.stderr}".lower()
        if "not compiled" not in probe_detail and "compiled with" not in probe_detail:
            return
    raise EngineUnavailable(
        f"engine '{normalized}' is not compiled into this binary. "
        "The selected model was not downloaded. Enable an ONNX Voxtype "
        "variant and select the model again."
    )


def enable_onnx() -> None:
    """Switch Voxtype's system binary after an explicit user request."""
    if not automatic_onnx_setup_supported():
        install_arm_onnx()
        return
    if shutil.which("pkexec") is None:
        raise RuntimeError("pkexec is required to enable the ONNX Voxtype variant")
    result = subprocess.run(
        ["pkexec", "voxtype", "setup", "onnx", "--enable"],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(detail or "ONNX setup was cancelled or failed")
    restart_daemon()


def install_arm_onnx() -> None:
    """Install the pinned upstream ARM ONNX binary after pkexec consent.

    The distro package owns /usr/bin/voxtype and its service uses that path.
    Keep the package intact: install the verified upstream ARM binary in
    /usr/local and add a user-service override pointing only this user's
    Voxtype daemon at it.
    """
    if platform.machine().lower() not in {"aarch64", "arm64"}:
        raise RuntimeError("The ARM ONNX installer was requested on a non-ARM machine")
    target = ARM_ONNX_INSTALL
    if not target.is_file() or sha256_file(target) != ARM_ONNX_SHA256:
        fd, temp_name = tempfile.mkstemp(prefix="voxtype-arm-onnx.", suffix=".part")
        os.close(fd)
        temp = Path(temp_name)
        try:
            request = urllib.request.Request(ARM_ONNX_URL, headers={"User-Agent": "omarchy-voxtype-enhance/0.1"})
            with urllib.request.urlopen(request, timeout=120) as response, temp.open("wb") as output:
                downloaded = 0
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    downloaded += len(chunk)
                    if downloaded > ARM_ONNX_MAX_SIZE:
                        raise RuntimeError("ARM ONNX binary exceeds its declared size")
                    output.write(chunk)
            if sha256_file(temp) != ARM_ONNX_SHA256:
                raise RuntimeError("ARM ONNX binary checksum mismatch")
            # Keep the verified bytes immutable across the privilege boundary.
            # Passing the temporary pathname to pkexec would allow a same-user
            # process to replace it after hashing but before root opens it.
            payload = temp.read_bytes()
            if len(payload) > ARM_ONNX_MAX_SIZE or hashlib.sha256(payload).hexdigest() != ARM_ONNX_SHA256:
                raise RuntimeError("ARM ONNX binary changed during verification")
            if shutil.which("pkexec") is None:
                raise RuntimeError("pkexec is required to install the ARM ONNX Voxtype binary")
            result = subprocess.run(
                ["pkexec", sys.executable, "-c", ARM_PRIVILEGED_INSTALL_HELPER],
                input=payload, check=False, capture_output=True,
            )
            if result.returncode != 0:
                detail = result.stderr.strip() or result.stdout.strip()
                raise RuntimeError(detail or "ARM ONNX installation was cancelled")
        finally:
            temp.unlink(missing_ok=True)

    ARM_ONNX_SERVICE_OVERRIDE.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    ARM_ONNX_SERVICE_OVERRIDE.write_text(
        "[Service]\nExecStart=\nExecStart=/usr/local/bin/voxtype daemon\n",
        encoding="utf-8",
    )
    daemon_reload = subprocess.run(
        ["systemctl", "--user", "daemon-reload"], check=False, capture_output=True, text=True
    )
    if daemon_reload.returncode != 0:
        detail = daemon_reload.stderr.strip() or daemon_reload.stdout.strip()
        raise RuntimeError(detail or "Could not reload the Voxtype user service")
    restart_daemon()


def ensure_voxtype_binary() -> None:
    """Install Omarchy's voxtype-bin package when the binary is missing.

    Every mutation below shells out to `voxtype`, so a missing binary would
    otherwise surface as a confusing FileNotFoundError deep in a download.
    Route the install through pkexec so the user explicitly authorizes it.
    """
    if shutil.which("voxtype") is not None:
        return
    missing = [name for name in ("pkexec", "pacman") if shutil.which(name) is None]
    if missing:
        raise RuntimeError(
            "Voxtype is not installed and cannot be installed automatically "
            f"(missing {', '.join(missing)}); install the voxtype-bin package manually"
        )
    result = subprocess.run(
        ["pkexec", "pacman", "-S", "--noconfirm", "--needed", "voxtype-bin"],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(detail or "Voxtype installation was cancelled or failed")
    if shutil.which("voxtype") is None:
        raise RuntimeError("voxtype-bin was installed but the binary is still unavailable")


def verify_model_selection(selected: dict[str, object]) -> None:
    """Reject false success when the requested engine/model did not persist."""
    actual = config_snapshot()
    expected_engine = str(selected["engine"])
    expected_model = str(selected["model"])
    if actual["engine"] != expected_engine or actual["model"] != expected_model:
        raise RuntimeError(
            "Voxtype configuration did not retain the selected model "
            f"(expected {expected_engine}/{expected_model}, got "
            f"{actual['engine']}/{actual['model']})"
        )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def ensure_model(model_id: str) -> None:
    """Download Sasayaki's pinned model files into Voxtype's ONNX layout."""
    spec = SASAYAKI_MODELS[model_id]
    target_dir = MODELS_DIR / spec["directory"]
    target_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    total_size = sum(item[2] for item in spec["files"])
    completed_size = 0

    def report(done: int, message: str) -> None:
        fraction = min(1.0, done / total_size) if total_size else 1.0
        print(f"VOXTYPE_ENHANCE_PROGRESS {fraction:.6f} {message}", file=sys.stderr, flush=True)

    report(0, "Checking model files")
    for filename, wanted_sha, wanted_size in spec["files"]:
        target = target_dir / filename
        if target.is_file() and target.stat().st_size == wanted_size and sha256_file(target) == wanted_sha:
            completed_size += wanted_size
            report(completed_size, f"Verified {filename}")
            continue
        fd, temp_name = tempfile.mkstemp(prefix=f".{filename}.", suffix=".part", dir=target_dir)
        os.close(fd)
        temp = Path(temp_name)
        try:
            request = urllib.request.Request(
                spec["source"] + filename,
                headers={"User-Agent": "omarchy-voxtype-enhance/0.1"},
            )
            with urllib.request.urlopen(request, timeout=60) as response, temp.open("wb") as output:
                downloaded = 0
                while True:
                    # Read at most one byte beyond the declared size.  This
                    # keeps a malicious or broken response from consuming
                    # unbounded disk space before the checksum/size check.
                    remaining = wanted_size - downloaded
                    chunk = response.read(min(1024 * 1024, remaining + 1))
                    if not chunk:
                        break
                    if len(chunk) > remaining:
                        raise RuntimeError(f"download exceeds declared size for {filename}")
                    output.write(chunk)
                    downloaded += len(chunk)
                    report(completed_size + downloaded, f"Downloading {filename}")
            if temp.stat().st_size != wanted_size or sha256_file(temp) != wanted_sha:
                raise RuntimeError(f"checksum or size mismatch for {filename}")
            os.replace(temp, target)
            completed_size += wanted_size
            report(completed_size, f"Verified {filename}")
        finally:
            temp.unlink(missing_ok=True)


def model_files_present(model_id: str) -> bool:
    spec = SASAYAKI_MODELS[model_id]
    model_dir = MODELS_DIR / spec["directory"]
    return all(
        (model_dir / filename).is_file() and (model_dir / filename).stat().st_size == size
        for filename, _, size in spec["files"]
    )


def reset_plugin_data() -> None:
    ensure_voxtype_binary()
    for spec in SASAYAKI_MODELS.values():
        model_dir = MODELS_DIR / spec["directory"]
        if model_dir.is_dir():
            shutil.rmtree(model_dir)

    set_engine("sensevoice")

    text = read_text()
    text = set_value(text, "sensevoice", "model", "small-int8")
    text = set_value(text, "sensevoice", "language", "zh")
    text = set_value(text, "output", "mode", "clipboard")
    text = set_value(text, "output", "pre_output_command", UNIVERSAL_SNAPSHOT)
    text = set_value(text, "output", "post_output_command", UNIVERSAL_PASTE_COMMAND)
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    CONFIG.write_text(text, encoding="utf-8")
    # Do not restart here: the default model was intentionally removed and
    # restarting would leave systemd in a crash loop until the user selects a
    # model and the downloader installs it again.


def config_snapshot() -> dict[str, str | bool]:
    text = read_text()
    engine = value(text, "", "engine", "whisper")
    section = engine.lower()
    model_default = {
        "sensevoice": "small-int8",
        "whisper": "small",
        "paraformer": "paraformer-zh",
        "parakeet": "parakeet-tdt-0.6b-v3",
        "moonshine": "moonshine-base",
    }.get(section, "")
    model = value(text, section, "model", model_default)
    model_id = next(
        (key for key, spec in SASAYAKI_MODELS.items()
         if spec["engine"] == section and spec["model"] == model
         and model_files_present(key)),
        "",
    )
    output_mode = value(text, "output", "mode", "type")
    post_output = value(text, "output", "post_output_command", "")
    if output_mode == "clipboard" and post_output == UNIVERSAL_PASTE_COMMAND:
        output_mode = "universal"
    return {
        "engine": engine.lower(),
        "model": model,
        "model_id": model_id,
        "installed_models": [key for key in SASAYAKI_MODELS if model_files_present(key)],
        "language": value(text, section, "language", "auto"),
        "mode": output_mode,
        "paste_keys": value(text, "output", "paste_keys", "ctrl+v"),
        "config_path": str(CONFIG),
    }


def set_setting(setting: str, new_value: str) -> None:
    ensure_voxtype_binary()
    text = read_text()
    current = config_snapshot()
    engine = current["engine"]
    if setting == "model":
        try:
            selected = SASAYAKI_MODELS[new_value]
        except KeyError as exc:
            raise ValueError(f"unsupported Sasayaki model: {new_value}") from exc

        # Download and verify before changing the active engine.  This leaves
        # the current Voxtype configuration usable if a download fails.
        check_engine_feature(str(selected["engine"]))
        ensure_model(new_value)

        # The user-facing model ID carries both pieces of information.  Keep
        # engine switching in Voxtype's own validated CLI, then write the
        # model in that engine's section.
        set_engine(str(selected["engine"]))
        # `voxtype config set` may normalize or add the engine section.  Do
        # not overwrite that fresh configuration with the snapshot read
        # before the engine switch (this used to switch Paraformer back to
        # SenseVoice immediately after a successful download).
        text = read_text()
        text = set_value(text, selected["engine"], "model", selected["model"])
        CONFIG.parent.mkdir(parents=True, exist_ok=True)
        CONFIG.write_text(text, encoding="utf-8")
        verify_model_selection(selected)
        restart_daemon()
        return
    elif setting == "engine":
        # Use Voxtype's own validated mutator so compiled-feature checks and
        # future config format changes remain owned by Voxtype.
        set_engine(new_value)
        restart_daemon()
        return
    elif setting == "language":
        text = set_value(text, str(engine), "language", new_value)
    elif setting == "mode":
        if new_value == "universal":
            text = set_value(text, "output", "mode", "clipboard")
            text = set_value(text, "output", "pre_output_command", UNIVERSAL_SNAPSHOT)
            text = set_value(text, "output", "post_output_command", UNIVERSAL_PASTE_COMMAND)
        else:
            text = set_value(text, "output", "mode", new_value)
            if value(text, "output", "pre_output_command", "") == UNIVERSAL_SNAPSHOT:
                text = remove_value(text, "output", "pre_output_command")
            if value(text, "output", "post_output_command", "") == UNIVERSAL_PASTE_COMMAND:
                text = remove_value(text, "output", "post_output_command")
    elif setting == "paste_keys":
        text = set_value(text, "output", "paste_keys", new_value)
    else:
        raise ValueError(f"unsupported setting: {setting}")
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    CONFIG.write_text(text, encoding="utf-8")
    restart_daemon()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["get", "set", "clear", "enable-onnx"])
    parser.add_argument("setting", nargs="?")
    parser.add_argument("value", nargs="?")
    args = parser.parse_args()
    try:
        if args.action == "get":
            print(json.dumps(config_snapshot(), ensure_ascii=False))
            return
        if args.action == "clear":
            reset_plugin_data()
            print(json.dumps(config_snapshot(), ensure_ascii=False))
            return
        if args.action == "enable-onnx":
            enable_onnx()
            print(json.dumps(config_snapshot(), ensure_ascii=False))
            return
        if not args.setting or args.value is None:
            raise SystemExit("set requires SETTING VALUE")
        if args.action == "set":
            set_setting(args.setting, args.value)
        print(json.dumps(config_snapshot(), ensure_ascii=False))
    except EngineUnavailable as error:
        print(json.dumps({
            "error": str(error),
            "requires_onnx": True,
            "onnx_setup_supported": onnx_install_supported(),
        }, ensure_ascii=False))
        raise SystemExit(1) from error
    except Exception as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False))
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
