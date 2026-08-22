#!/usr/bin/env python3
"""Small, dependency-free Voxtype config bridge for the Quickshell widget."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
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
    """Switch engine through Voxtype so binary feature gates stay authoritative."""
    result = subprocess.run(
        ["voxtype", "config", "set", "engine", engine.lower()],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(detail or f"Voxtype rejected the {engine} engine")


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
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
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
    """Remove only models owned by this plugin and restore its defaults."""
    for spec in SASAYAKI_MODELS.values():
        model_dir = MODELS_DIR / spec["directory"]
        if model_dir.is_dir():
            shutil.rmtree(model_dir)

    set_engine("sensevoice")

    text = read_text()
    text = set_value(text, "sensevoice", "model", "small-int8")
    text = set_value(text, "sensevoice", "language", "zh")
    text = set_value(text, "output", "mode", "clipboard")
    text = set_value(text, "output", "post_output_command", str(UNIVERSAL_PASTE))
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
    if output_mode == "clipboard" and post_output == str(UNIVERSAL_PASTE):
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
            text = set_value(text, "output", "post_output_command", str(UNIVERSAL_PASTE))
        else:
            text = set_value(text, "output", "mode", new_value)
            if value(text, "output", "post_output_command", "") == str(UNIVERSAL_PASTE):
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
    parser.add_argument("action", choices=["get", "set", "clear"])
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
        if not args.setting or args.value is None:
            raise SystemExit("set requires SETTING VALUE")
        set_setting(args.setting, args.value)
        print(json.dumps(config_snapshot(), ensure_ascii=False))
    except Exception as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False))
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
