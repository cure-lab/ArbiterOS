from __future__ import annotations

import os
import signal
import socket
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, IO, Optional

from arbiteros_kernel.tui.config_catalog import kernel_root
from arbiteros_kernel.session_traces import reset_session


@dataclass
class ProxyProcess:
    process: subprocess.Popen[str]
    log_handle: IO[str]
    started_by_tui: bool


def _port_open(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _http_ready(base_url: str, timeout: float = 1.0) -> bool:
    for suffix in ("/health", "/health/liveliness", "/"):
        url = base_url.rstrip("/") + suffix
        try:
            with urllib.request.urlopen(url, timeout=timeout) as resp:
                if 200 <= resp.status < 500:
                    return True
        except urllib.error.HTTPError as exc:
            if exc.code < 500:
                return True
        except Exception:
            continue
    return False


def is_proxy_ready(host: str = "127.0.0.1", port: int = 4000) -> bool:
    if not _port_open(host, port, timeout=0.5):
        return False
    return _http_ready(f"http://{host}:{port}", timeout=1.0)


def wait_for_proxy(
    *,
    host: str = "127.0.0.1",
    port: int = 4000,
    timeout_sec: float = 180.0,
    poll_sec: float = 0.5,
) -> bool:
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        if is_proxy_ready(host=host, port=port):
            return True
        time.sleep(poll_sec)
    return False


def _uv_prefix() -> list[str]:
    return ["uv", "run"]


def warm_skills(cwd: Path) -> None:
    subprocess.run(
        [*_uv_prefix(), "python", "-m", "arbiteros_kernel.warm_skill_trust"],
        cwd=str(cwd),
        check=False,
    )


def listener_pids(port: int) -> list[int]:
    """PIDs currently LISTENing on TCP ``port`` (best-effort via lsof)."""
    try:
        out = subprocess.check_output(
            ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return []
    pids: list[int] = []
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            pids.append(int(line))
        except ValueError:
            continue
    return sorted(set(pids))


def _signal_pid_tree(pid: int, sig: int) -> None:
    try:
        os.killpg(pid, sig)
        return
    except (ProcessLookupError, PermissionError, OSError):
        pass
    try:
        os.kill(pid, sig)
    except (ProcessLookupError, PermissionError, OSError):
        pass


def free_port(
    port: int,
    *,
    host: str = "127.0.0.1",
    timeout_sec: float = 10.0,
    on_status: Optional[Callable[[str], None]] = None,
) -> bool:
    """Stop whatever is listening on ``port`` so ArbiterOS can own the Kernel."""
    pids = listener_pids(port)
    if not pids and not _port_open(host, port, timeout=0.3):
        return True
    if on_status and pids:
        on_status(
            f"Port {port} is in use (pid {', '.join(map(str, pids))}); "
            "stopping it so ArbiterOS owns the Kernel…"
        )
    for pid in pids:
        _signal_pid_tree(pid, signal.SIGTERM)
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        if not listener_pids(port) and not _port_open(host, port, timeout=0.2):
            return True
        time.sleep(0.2)
    # Force kill remaining listeners.
    for pid in listener_pids(port):
        _signal_pid_tree(pid, signal.SIGKILL)
    time.sleep(0.3)
    return not listener_pids(port)


def start_proxy(
    *,
    host: str = "0.0.0.0",
    port: int = 4000,
    cwd: Optional[Path] = None,
) -> ProxyProcess:
    root = cwd or kernel_root()
    log_dir = root / "log"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "proxy.log"
    log_handle = open(log_path, "a", encoding="utf-8")
    env = os.environ.copy()
    env["ARBITEROS_TUI"] = "1"
    env.setdefault("LITELLM_LOG", "ERROR")
    cmd = [
        *_uv_prefix(),
        "litellm",
        "--config",
        "litellm_config.yaml",
        "--host",
        host,
        "--port",
        str(port),
        "--run_hypercorn",
    ]
    process = subprocess.Popen(
        cmd,
        cwd=str(root),
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        env=env,
        text=True,
        start_new_session=True,  # own process group for clean stop on TUI quit
    )
    return ProxyProcess(process=process, log_handle=log_handle, started_by_tui=True)


def ensure_proxy_running(
    *,
    host: str = "127.0.0.1",
    port: int = 4000,
    start_if_missing: bool = True,
    own_lifecycle: bool = True,
    on_status: Optional[Callable[[str], None]] = None,
) -> tuple[bool, Optional[ProxyProcess]]:
    """
    Ensure a Kernel proxy is up.

    When ``own_lifecycle`` is True (default for ``poe arbiteros``):
    reclaim ``port`` if needed, start a fresh proxy owned by this TUI, and
    return that process so quit can shut it down.
    """
    if not start_if_missing:
        ready = is_proxy_ready(host=host, port=port)
        if ready and on_status:
            on_status(f"Proxy already listening on port {port}.")
        return ready, None

    if own_lifecycle:
        if is_proxy_ready(host=host, port=port) or listener_pids(port):
            if not free_port(port, host=host, on_status=on_status):
                if on_status:
                    on_status(
                        f"Could not free port {port}. Stop the other process and retry."
                    )
                return False, None
        root = kernel_root()
        if on_status:
            on_status("Warming skill trust cache…")
        warm_skills(root)
        if on_status:
            on_status("Starting ArbiterOS Kernel proxy…")
        reset_session()
        proc = start_proxy(port=port)
        if on_status:
            on_status("Waiting for proxy to become ready…")
        ready = wait_for_proxy(host=host, port=port)
        if not ready:
            if proc.process.poll() is not None and on_status:
                on_status(
                    f"Proxy exited early (code {proc.process.returncode}). See log/proxy.log"
                )
            return False, proc
        return True, proc

    # Legacy attach-only path: reuse an already-running proxy.
    if is_proxy_ready(host=host, port=port):
        if on_status:
            on_status(f"Proxy already listening on port {port}.")
        return True, None
    return False, None


def stop_proxy(proxy: Optional[ProxyProcess]) -> None:
    if proxy is None:
        return
    proc = proxy.process
    if proc.poll() is None:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            proc.terminate()
        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError, OSError):
                proc.kill()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                pass
    try:
        proxy.log_handle.close()
    except Exception:
        pass
    reset_session()
