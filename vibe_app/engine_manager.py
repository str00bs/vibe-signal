from __future__ import annotations

import asyncio
import platform
import stat
import sys
import urllib.request
import zipfile
from pathlib import Path
from typing import Optional

from .config import Config

BIN_DIR = Path(__file__).resolve().parent / "bin"
ENGINE_VERSION = "4.0.2"
RELEASE_TAG = f"intiface-engine-{ENGINE_VERSION}"
RELEASE_BASE = f"https://github.com/buttplugio/buttplug/releases/download/{RELEASE_TAG}"

# platform.system(), platform.machine() -> release asset suffix
_ASSET_MAP = {
    ("Darwin", "arm64"): "macos-arm64",
    ("Linux", "x86_64"): "linux-x64",
    ("Linux", "aarch64"): "linux-arm64",
    ("Windows", "AMD64"): "win-x64",
}


def _asset_name() -> str:
    key = (platform.system(), platform.machine())
    suffix = _ASSET_MAP.get(key)
    if suffix is None:
        raise RuntimeError(
            f"No prebuilt intiface-engine binary for {key}. "
            f"Install it yourself (e.g. `cargo install intiface-engine`) and set "
            f"`engine_binary_path` in config.yaml to point at it."
        )
    return f"intiface-engine-v{ENGINE_VERSION}-{suffix}.zip"


def _binary_name() -> str:
    return "intiface-engine.exe" if platform.system() == "Windows" else "intiface-engine"


def ensure_binary(config: Config) -> Path:
    """Return a path to a usable intiface-engine binary, downloading it if needed."""
    if config.engine_binary_path:
        path = Path(config.engine_binary_path).expanduser()
        if not path.exists():
            raise RuntimeError(f"engine_binary_path is set but does not exist: {path}")
        return path

    BIN_DIR.mkdir(parents=True, exist_ok=True)
    target = BIN_DIR / _binary_name()
    if target.exists():
        return target

    asset = _asset_name()
    url = f"{RELEASE_BASE}/{asset}"
    zip_path = BIN_DIR / asset
    print(f"[engine] downloading {url}", file=sys.stderr)
    urllib.request.urlretrieve(url, zip_path)

    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(BIN_DIR)
    zip_path.unlink(missing_ok=True)

    if not target.exists():
        # some archives nest the binary in a subfolder; find it
        found = next(BIN_DIR.rglob(_binary_name()), None)
        if found is None:
            raise RuntimeError(f"Downloaded {asset} but couldn't find {_binary_name()} inside it")
        found.rename(target)

    target.chmod(target.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return target


class EngineProcess:
    def __init__(self, config: Config):
        self.config = config
        self._proc: Optional[asyncio.subprocess.Process] = None

    async def start(self) -> None:
        binary = ensure_binary(self.config)
        args = [
            str(binary),
            "--websocket-port",
            str(self.config.engine_websocket_port),
            "--use-bluetooth-le",
            "--server-name",
            "vibe-app",
        ]
        print(f"[engine] starting: {' '.join(args)}", file=sys.stderr)
        self._proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        if self.config.debug:
            asyncio.create_task(self._pump_logs())
        await self._wait_until_listening()

    async def _pump_logs(self) -> None:
        assert self._proc and self._proc.stdout
        async for line in self._proc.stdout:
            print(f"[engine] {line.decode(errors='replace').rstrip()}", file=sys.stderr)

    async def _wait_until_listening(self, timeout_s: float = 15.0) -> None:
        deadline = asyncio.get_event_loop().time() + timeout_s
        while asyncio.get_event_loop().time() < deadline:
            assert self._proc is not None
            if self._proc.returncode is not None:
                hint = ""
                if sys.platform == "darwin":
                    hint = (
                        "\nOn macOS, intiface-engine crashes immediately if the terminal app "
                        "running this hasn't been granted Bluetooth access: open System Settings "
                        "-> Privacy & Security -> Bluetooth, click '+', and add your terminal app "
                        "(Terminal/iTerm/etc), then re-run."
                    )
                raise RuntimeError(
                    f"intiface-engine exited early (code {self._proc.returncode}) before "
                    f"opening its websocket port.{hint}"
                )
            try:
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection("127.0.0.1", self.config.engine_websocket_port),
                    timeout=0.5,
                )
                writer.close()
                await writer.wait_closed()
                return
            except (OSError, asyncio.TimeoutError):
                await asyncio.sleep(0.3)
        raise RuntimeError(
            f"intiface-engine didn't start listening on port {self.config.engine_websocket_port} "
            f"within {timeout_s}s"
        )

    async def stop(self) -> None:
        if self._proc is None or self._proc.returncode is not None:
            return
        self._proc.terminate()
        try:
            await asyncio.wait_for(self._proc.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            self._proc.kill()
            await self._proc.wait()
