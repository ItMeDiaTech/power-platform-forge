"""A long-lived Windows PowerShell 5.1 window that holds the Power Apps maker sign-in.

The maker module keeps its sign-in only inside the process that ran Add-PowerAppsAccount, so a fresh
hidden process per call would open a sign-in window and wait. `ppx auth login --backend maker` opens
one visible PowerShell window instead: the user signs in there once, and the window then runs ppx's
maker calls in that signed-in session until it is closed, ended with `ppx auth logout --backend maker`,
or idle for IDLE_SECONDS. If the module needs a fresh sign-in meanwhile, it asks in that window.

Protocol: one connection per call, one UTF-8 JSON line each way. {"script", "data"} runs `script` with
`$in` set to `data`; the reply is {"ok": true, "out": <JSON text or null>} or {"ok": false, "error",
"category"}. {"ping": true} and {"stop": true} check and end the window.

Safety: the pipe allows only this Windows user and refuses network logons; the window always holds a
listening instance, so the name is never free for another process to take; the client opens the pipe
with impersonation limited to identification and sends nothing unless the server process is the
window recorded in <data>/maker-session.json.
"""

from __future__ import annotations

import base64
import json
import os
import secrets
import subprocess
import sys
import threading
import time
from pathlib import Path

from ppx import datadir, proc
from ppx.errors import PpxError, SetupNeeded

INFO_FILE = "maker-session.json"
IDLE_SECONDS = 8 * 3600  # a working day without a call ends the window
READY_SECONDS = 300  # how long `ppx auth login --backend maker` waits for the sign-in
CONNECT_SECONDS = 10  # both of the window's pipe instances in use: wait this long for one to free up

_ERROR_FILE_NOT_FOUND, _ERROR_PIPE_BUSY = 2, 231


class Busy(PpxError):
    """The window is running but did not answer in time: busy with another call, or asking for a sign-in."""


def ps_quote(value) -> str:
    """A PowerShell single-quoted literal: nothing inside it is expanded."""
    return "'" + str(value).replace("'", "''") + "'"


def helper_script(pipe: str, info: Path, idle_seconds: int = IDLE_SECONDS, sign_in: bool = True) -> str:
    """The window's PowerShell. `sign_in=False` skips the maker module, for tests on a PC without it."""
    sign_in_part = ("Import-Module Microsoft.PowerApps.PowerShell; "
                    "Write-Host 'Sign in to Power Apps in the window that opens...'; "
                    "try { Add-PowerAppsAccount | Out-Null } catch { "
                    "Write-Host ('Sign-in failed: ' + $_.Exception.Message) -ForegroundColor Red; "
                    "Read-Host 'Press Enter to close'; exit 1 }; ") if sign_in else ""
    return (
        "$ErrorActionPreference = 'Stop'; $ProgressPreference = 'SilentlyContinue'; "
        "$Host.UI.RawUI.WindowTitle = 'ppx maker session'; "
        f"$pipeName = {ps_quote(pipe)}; $info = {ps_quote(info)}; $idle = [TimeSpan]::FromSeconds({int(idle_seconds)}); "
        + sign_in_part +
        # This Windows user only, and never over the network (S-1-5-2 is the NETWORK logon group).
        "$security = New-Object System.IO.Pipes.PipeSecurity; "
        "$security.AddAccessRule((New-Object System.IO.Pipes.PipeAccessRule("
        "[System.Security.Principal.WindowsIdentity]::GetCurrent().User, 'FullControl', 'Allow'))); "
        "$security.AddAccessRule((New-Object System.IO.Pipes.PipeAccessRule("
        "(New-Object System.Security.Principal.SecurityIdentifier('S-1-5-2')), 'FullControl', 'Deny'))); "
        "$utf8 = New-Object System.Text.UTF8Encoding $false; "
        "function New-Listener { $s = New-Object System.IO.Pipes.NamedPipeServerStream($pipeName, 'InOut', 2, "
        "'Byte', 'Asynchronous', 65536, 65536, $security); @{ stream = $s; wait = $s.BeginWaitForConnection($null, $null) } }; "
        "$current = New-Listener; "
        "$record = @{ pipe = $pipeName; pid = $PID; started = (Get-Date).ToUniversalTime().ToString('o') }; "
        "[IO.File]::WriteAllText($info, (ConvertTo-Json -InputObject $record -Compress), $utf8); "
        "Write-Host 'ppx maker session ready. Leave this window open; close it to end the session.' "
        "-ForegroundColor Green; "
        "$running = $true; "
        "try { while ($running) { "
        "if (-not $current.wait.AsyncWaitHandle.WaitOne($idle)) { break }; "
        "$server = $current.stream; "
        "try { $server.EndWaitForConnection($current.wait) } catch { $server.Dispose(); $current = New-Listener; continue }; "
        # The next listener exists before this call is answered, so the pipe name is never free.
        "$current = New-Listener; "
        "$reader = New-Object System.IO.StreamReader($server, $utf8); "
        "$writer = New-Object System.IO.StreamWriter($server, $utf8); "
        "try { $line = $reader.ReadLine() } catch { $line = $null }; "
        "if ([string]::IsNullOrWhiteSpace($line)) { $server.Dispose(); continue }; "
        "try { $req = ConvertFrom-Json $line; "
        "if ($req.stop) { $reply = @{ ok = $true; stopped = $true }; $running = $false } "
        "elseif ($req.ping) { $reply = @{ ok = $true; pid = $PID } } "
        "else { $in = $req.data; $out = & ([scriptblock]::Create([string]$req.script)); "
        "$reply = @{ ok = $true; out = $(if ($null -ne $out) { ConvertTo-Json -InputObject $out -Depth 6 -Compress } "
        "else { $null }) } } } "
        "catch { $reply = @{ ok = $false; error = [string]$_.Exception.Message; "
        "category = [string]$_.CategoryInfo.Category } }; "
        "try { $writer.WriteLine((ConvertTo-Json -InputObject $reply -Compress -Depth 3)); $writer.Flush(); "
        "$server.WaitForPipeDrain() } catch { }; "
        "$server.Dispose() } } "
        # Remove the record only while it is still this window's: a newer window may have replaced it.
        "finally { $current.stream.Dispose(); "
        "try { if ((ConvertFrom-Json ([IO.File]::ReadAllText($info))).pipe -eq $pipeName) { "
        "Remove-Item -LiteralPath $info } } catch { } }"
    )


def _info_path(data_dir: Path | None) -> Path | None:
    if data_dir is None:
        try:
            data_dir = datadir.resolve()
        except datadir.DataDirMissing:
            return None
    return Path(data_dir) / INFO_FILE


def current(data_dir: Path | None = None) -> dict | None:
    """The recorded session {pipe, pid, started}, or None. Whether it still answers is known only by calling it."""
    path = _info_path(data_dir)
    try:
        info = json.loads(path.read_text(encoding="utf-8-sig")) if path else None
    except (OSError, ValueError):
        return None
    if not isinstance(info, dict) or not str(info.get("pipe", "")).startswith("ppx-maker-"):
        return None
    return info


def _kernel32():
    import ctypes
    from ctypes import wintypes

    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
                              wintypes.DWORD, wintypes.HANDLE]
    k.CreateFileW.restype = wintypes.HANDLE
    k.GetNamedPipeServerProcessId.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.ULONG)]
    k.GetNamedPipeServerProcessId.restype = wintypes.BOOL
    k.OpenThread.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k.OpenThread.restype = wintypes.HANDLE
    k.CancelSynchronousIo.argtypes = [wintypes.HANDLE]
    k.CloseHandle.argtypes = [wintypes.HANDLE]
    return k


def _connect(info: dict):
    """The pipe as a binary file, or None when no window serves it. Sends nothing to a server that is not
    the recorded window, and never lets the server act as this user (identification-level impersonation)."""
    import ctypes
    import msvcrt
    from ctypes import wintypes

    k = _kernel32()
    path = "\\\\.\\pipe\\" + info["pipe"]
    deadline = time.monotonic() + CONNECT_SECONDS
    while True:
        handle = k.CreateFileW(path, 0x80000000 | 0x40000000, 0, None, 3,  # GENERIC_READ|WRITE, OPEN_EXISTING
                               0x00100000 | 0x00010000, None)  # SECURITY_SQOS_PRESENT | SECURITY_IDENTIFICATION
        if handle not in (None, wintypes.HANDLE(-1).value):
            break
        err = ctypes.get_last_error()
        if err == _ERROR_FILE_NOT_FOUND:
            return None
        if err != _ERROR_PIPE_BUSY:
            raise PpxError(f"the maker session window could not be reached (Windows error {err})")
        if time.monotonic() > deadline:
            raise Busy("the maker session window is busy with other calls; retry in a moment")
        time.sleep(0.05)
    server = wintypes.ULONG()
    if not k.GetNamedPipeServerProcessId(handle, ctypes.byref(server)) or server.value != info.get("pid"):
        k.CloseHandle(handle)
        raise PpxError(f"the pipe recorded for the maker session is served by another process ({server.value}, "
                       f"not {info.get('pid')}), so nothing was sent; close any ppx maker session window and run "
                       "`ppx auth login --backend maker` again")
    return os.fdopen(msvcrt.open_osfhandle(handle, os.O_BINARY), "r+b", buffering=0)


def _request(info: dict, message: dict, timeout: float) -> dict | None:
    """Send one message; None when no window serves the pipe. Raises Busy when it gives no answer in time."""
    pipe = _connect(info)
    if pipe is None:
        return None
    chunks: list[bytes] = []
    failure: list[BaseException] = []
    thread_id: list[int] = []

    def read() -> None:
        thread_id.append(threading.get_native_id())
        try:
            pipe.write(json.dumps(message).encode("utf-8") + b"\n")
            while chunk := pipe.read(65536):
                chunks.append(chunk)
        except (OSError, ValueError) as exc:  # cancelled after a timeout, or the window went away
            failure.append(exc)

    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    reader.join(timeout)
    if reader.is_alive():
        # The read is synchronous: closing the pipe would wait for it, so cancel it on its own thread first.
        k = _kernel32()
        handle = k.OpenThread(0x0001, False, thread_id[0])  # THREAD_TERMINATE, which CancelSynchronousIo needs
        if handle:
            k.CancelSynchronousIo(handle)
            k.CloseHandle(handle)
        reader.join(2)
        pipe.close()
        raise Busy(f"the maker session window gave no answer within {timeout:g} s; it may still be working on it, "
                   "or asking you to sign in again (look at the window)")
    pipe.close()
    text = b"".join(chunks).decode("utf-8", "replace").strip()
    if not text:
        detail = f": {failure[0]}" if failure else ""
        raise PpxError(f"the maker session window closed the call without an answer{detail}")
    try:
        return json.loads(text)
    except ValueError as exc:
        raise PpxError(f"the maker session window answered with something that is not JSON: {text[:120]}") from exc


def call(script: str, data: dict, timeout: float, data_dir: Path | None = None) -> proc.Proc | None:
    """Run `script` in the signed-in window as a Proc shaped like a PowerShell run; None without a window."""
    info = current(data_dir)
    if info is None:
        return None
    reply = _request(info, {"script": script, "data": data}, timeout)
    if reply is None:  # the window was closed without removing its record
        _forget(data_dir, info["pipe"])
        return None
    argv = ["ppx maker session", info["pipe"]]
    if reply.get("ok"):
        return proc.Proc(argv, 0, reply.get("out") or "", "")
    return proc.Proc(argv, 1, json.dumps({"error": reply.get("error"), "category": reply.get("category")}), "")


def status(data_dir: Path | None = None, timeout: float = 5) -> dict:
    """{running, busy?, started, pid} for the doctor and the verbs; never raises."""
    info = current(data_dir)
    if info is None:
        return {"running": False}
    known = {"started": info.get("started"), "pid": info.get("pid")}
    try:
        reply = _request(info, {"ping": True}, timeout)
    except Busy:
        return {"running": True, "busy": True, **known}
    except PpxError as exc:
        return {"running": False, "error": exc.message}
    if reply is None:
        _forget(data_dir, info["pipe"])
        return {"running": False}
    return {"running": True, **known}


def start(data_dir: Path, *, visible: bool = True, sign_in: bool = True, timeout: float = READY_SECONDS,
          idle_seconds: int = IDLE_SECONDS) -> dict:
    """Open the window and wait until it is signed in and listening; return its record."""
    if sys.platform != "win32":
        raise SetupNeeded("the Power Apps maker module runs only in Windows PowerShell 5.1")
    exe = proc.require("powershell.exe", "Windows PowerShell 5.1 ships with Windows 10 and 11")
    running = status(data_dir)
    if running["running"]:  # busy counts: a second window would leave one of them signed in and untracked
        return {**running, "already_running": True}
    info = Path(data_dir) / INFO_FILE
    info.unlink(missing_ok=True)
    pipe = f"ppx-maker-{secrets.token_hex(16)}"
    encoded = base64.b64encode(helper_script(pipe, info, idle_seconds, sign_in).encode("utf-16-le")).decode("ascii")
    # -EncodedCommand is a command, not a script file, so the execution policy does not block the window itself;
    # the maker module's .psm1 files still need a policy that allows module scripts (PLATFORM-FACTS 23).
    flags = subprocess.CREATE_NEW_CONSOLE if visible else subprocess.CREATE_NO_WINDOW
    quiet = {} if visible else {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    window = subprocess.Popen([exe, "-NoProfile", "-EncodedCommand", encoded], creationflags=flags, **quiet)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        record = current(data_dir)
        if record and record.get("pipe") == pipe:
            return {"running": True, "started": record.get("started"), "pid": record.get("pid")}
        if window.poll() is not None:
            raise PpxError(f"the maker session window closed before it was ready (exit {window.returncode}); "
                           "the sign-in failed or was cancelled")
        time.sleep(0.25)
    raise PpxError(f"the maker sign-in did not finish within {timeout:g} s; finish it in the window, then run "
                   "`ppx auth doctor` to check the session")


def stop(data_dir: Path | None = None) -> bool:
    """End the window; True when one was running."""
    info = current(data_dir)
    if info is None:
        return False
    try:
        reply = _request(info, {"stop": True}, 10)
    except PpxError:
        reply = None
    _forget(data_dir, info["pipe"])
    return bool(reply and reply.get("stopped"))


def _forget(data_dir: Path | None, pipe: str) -> None:
    """Remove the record, but only while it still names `pipe` (a newer window may have replaced it)."""
    record = current(data_dir)
    path = _info_path(data_dir)
    if path and record and record.get("pipe") == pipe:
        path.unlink(missing_ok=True)
