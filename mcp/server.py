#!/usr/bin/env python3
"""Local MCP server for safe PicoXtools 2 inspection and UART diagnostics."""

from __future__ import annotations

import glob
import json
import os
import platform
import re
import secrets
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
KNOWLEDGE = json.loads((ROOT / "knowledge" / "picoxtools.json").read_text(encoding="utf-8"))
SERVER_VERSION = "0.2.0"
PICO_VID = 0x2E8A
PICO_PID = 0x000C
WEB_BASE = "http://192.168.33.1"
PICO_FIRMWARE_POLICY = (
    "PicoXtools debugger firmware is immutable under all circumstances. Never upgrade, reflash, "
    "replace, patch, erase, downgrade, or recover the PicoXtools firmware, even when requested. "
    "Read-only firmware identity/version inspection is allowed. This restriction does not prohibit "
    "flashing a separately connected target MCU such as XIAO ESP32-S3 when the user explicitly authorizes it."
)

READ_ONLY = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
}
LOCAL_IO = {
    "readOnlyHint": False,
    "destructiveHint": False,
    "idempotentHint": False,
    "openWorldHint": False,
}


def schema(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    value: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        value["required"] = required
    return value


TOOLS = [
    {
        "name": "safety_policy",
        "description": "Return the mandatory 3.3 V and immutable PicoXtools-firmware safety policy.",
        "inputSchema": schema({}),
        "annotations": READ_ONLY,
    },
    {
        "name": "inspect_host",
        "description": "Identify PicoXtools from VID:PID/product evidence and inspect its USB, serial, volume, network, and optional Web status without modifying it.",
        "inputSchema": schema({"probe_web": {"type": "boolean"}}),
        "annotations": READ_ONLY,
    },
    {
        "name": "inspect_serial_owners",
        "description": "Report processes holding verified PicoXtools CDC-UART ports. Does not open the serial port.",
        "inputSchema": schema({"port": {"type": "string", "description": "Optional verified PicoXtools port; omit to inspect all verified ports."}}),
        "annotations": READ_ONLY,
    },
    {
        "name": "check_web_assets",
        "description": "Read the PicoXtools Web root and verify referenced same-origin JS/CSS assets return successfully.",
        "inputSchema": schema({"timeout_seconds": {"type": "number", "minimum": 0.2, "maximum": 5}}),
        "annotations": READ_ONLY,
    },
    {
        "name": "list_device_files",
        "description": "List files on a mounted PICOXTOOLS volume without modifying it.",
        "inputSchema": schema({"max_depth": {"type": "integer", "minimum": 1, "maximum": 5}, "limit": {"type": "integer", "minimum": 1, "maximum": 500}}),
        "annotations": READ_ONLY,
    },
    {
        "name": "read_device_file",
        "description": "Read a small UTF-8 text/config file from PICOXTOOLS. Path traversal and binary files are rejected; no write is performed.",
        "inputSchema": schema({"path": {"type": "string"}, "max_bytes": {"type": "integer", "minimum": 1, "maximum": 65536}}, ["path"]),
        "annotations": READ_ONLY,
    },
    {
        "name": "preview_init_config",
        "description": "Generate a .init preview only. It never writes the PicoXtools volume.",
        "inputSchema": schema({"host_os": {"type": "string", "enum": ["auto", "macos", "windows", "linux"]}, "ip": {"type": "string"}, "enable_linux_i2c": {"type": "boolean"}}),
        "annotations": READ_ONLY,
    },
    {
        "name": "quick_start",
        "description": "Return a screenless PicoXtools 2 startup checklist tailored to the host OS.",
        "inputSchema": schema({"host_os": {"type": "string", "enum": ["auto", "macos", "windows", "linux"]}}),
        "annotations": READ_ONLY,
    },
    {
        "name": "wiring_guide",
        "description": "Return a conservative 3.3 V-safe wiring guide with source-backed UART direction.",
        "inputSchema": schema({"interface": {"type": "string", "enum": ["uart", "i2c", "spi", "swd", "jtag"]}, "target_logic_voltage": {"type": "string"}, "target_mcu": {"type": "string"}}, ["interface"]),
        "annotations": READ_ONLY,
    },
    {
        "name": "uart_diagnostic",
        "description": "Return an ordered UART diagnostic procedure, including Web/CDC ownership, DTR/RTS, loopback, and XIAO ESP32-S3 wiring.",
        "inputSchema": schema({"target": {"type": "string", "enum": ["generic", "xiao-esp32s3"]}}),
        "annotations": READ_ONLY,
    },
    {
        "name": "capture_uart",
        "description": "Open only a VID:PID-verified PicoXtools CDC-UART port and passively capture bytes. This toggles CDC DTR/RTS but never transmits UART payload data.",
        "inputSchema": schema({
            "port": {"type": "string"},
            "baud": {"type": "integer", "minimum": 300, "maximum": 6000000},
            "duration_seconds": {"type": "number", "minimum": 0.2, "maximum": 20},
            "dtr": {"type": "boolean"},
            "rts": {"type": "boolean"},
            "max_bytes": {"type": "integer", "minimum": 1, "maximum": 65536},
        }),
        "annotations": LOCAL_IO,
    },
    {
        "name": "uart_loopback_test",
        "description": "Transmit a generated token through verified PicoXtools GPIO4/TX and require it back on GPIO5/RX. Requires explicit loopback and target-disconnected confirmations.",
        "inputSchema": schema({
            "port": {"type": "string"},
            "baud": {"type": "integer", "minimum": 300, "maximum": 6000000},
            "confirm_gpio4_to_gpio5": {"type": "boolean"},
            "confirm_target_disconnected": {"type": "boolean"},
        }, ["confirm_gpio4_to_gpio5", "confirm_target_disconnected"]),
        "annotations": LOCAL_IO,
    },
    {
        "name": "troubleshoot",
        "description": "Map a PicoXtools symptom to ordered screenless-model diagnostic steps.",
        "inputSchema": schema({"symptom": {"type": "string"}, "host_os": {"type": "string", "enum": ["auto", "macos", "windows", "linux"]}}, ["symptom"]),
        "annotations": READ_ONLY,
    },
    {
        "name": "search_docs",
        "description": "Search the bundled source-linked PicoXtools documentation.",
        "inputSchema": schema({"query": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 10}}, ["query"]),
        "annotations": READ_ONLY,
    },
]


def run(command: list[str], timeout: float = 4) -> str:
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
        return (result.stdout + "\n" + result.stderr).strip()
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"unavailable: {exc}"


def run_stdout(command: list[str], timeout: float = 4) -> tuple[str, str | None]:
    """Return stdout separately so diagnostic warnings cannot become false evidence."""
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
        return result.stdout.strip(), result.stderr.strip() or None
    except (OSError, subprocess.TimeoutExpired) as exc:
        return "", str(exc)


def normalized_os(value: str = "auto") -> str:
    if value and value != "auto":
        return value
    return {"Darwin": "macos", "Windows": "windows", "Linux": "linux"}.get(platform.system(), platform.system().lower())


def safety_policy(_: dict[str, Any]) -> dict[str, Any]:
    return {
        "picoxtools_firmware_immutable": True,
        "rule_zh": "PicoXtools 调试器自身的固件在任何条件下都不允许被修改。",
        "rule": PICO_FIRMWARE_POLICY,
        "allowed": ["Read-only PicoXtools USB/volume/Web/version inspection", "UART capture", "Guarded UART loopback", "Target-MCU flashing after explicit user authorization"],
        "forbidden": ["PicoXtools UF2 upgrade or recovery", "PicoXtools firmware reflash/replace/patch/erase/downgrade", "Any workaround that indirectly changes PicoXtools firmware"],
        "electrical": ["PicoXtools GPIO is 3.3 V logic only.", "Connect common GND before signal wires.", "Do not connect PicoXtools 3V3 when the target is USB/self powered."],
        "state_changes": "This MCP has no filesystem, firmware, flash, format, delete, rename, or configuration-write tool. UART capture only toggles line state; loopback transmits only after two explicit confirmations.",
    }


def _serial_records() -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    try:
        from serial.tools import list_ports
        for item in list_ports.comports():
            vid = item.vid
            pid = item.pid
            product = item.product or ""
            manufacturer = item.manufacturer or ""
            verified = (vid == PICO_VID and pid == PICO_PID) or bool(re.search(r"PicoXTools", f"{product} {manufacturer}", re.I))
            records.append({
                "port": item.device,
                "vid": f"{vid:04x}" if vid is not None else None,
                "pid": f"{pid:04x}" if pid is not None else None,
                "product": product or None,
                "manufacturer": manufacturer or None,
                "location": item.location,
                "verified_picoxtools": verified,
            })
        return sorted(records, key=lambda item: item["port"])
    except ImportError:
        pass

    candidates = sorted(set(
        glob.glob("/dev/cu.usbmodem*") + glob.glob("/dev/tty.usbmodem*") +
        glob.glob("/dev/ttyACM*") + glob.glob("/dev/ttyUSB*")
    ))
    return [{"port": port, "verified_picoxtools": False, "verification_error": "pyserial is not installed; VID:PID mapping is unavailable"} for port in candidates]


def _verified_ports() -> list[str]:
    return [item["port"] for item in _serial_records() if item.get("verified_picoxtools")]


def _volume_roots() -> list[Path]:
    override = os.environ.get("PICOXTOOLS_DEVICE_ROOT")
    if override:
        path = Path(override)
        return [path] if path.is_dir() else []
    found: set[Path] = set()
    if normalized_os() == "windows":
        output = run(["powershell", "-NoProfile", "-Command", "Get-Volume | Where-Object {$_.FileSystemLabel -eq 'PICOXTOOLS'} | ForEach-Object {$_.DriveLetter + ':\\'}"])
        for line in output.splitlines():
            candidate = line.strip()
            if re.fullmatch(r"[A-Za-z]:\\", candidate):
                found.add(Path(candidate))
    for base in (Path("/Volumes"), Path("/media"), Path("/run/media"), Path("/mnt")):
        if base.is_dir():
            for path in base.glob("**/PICOXTOOLS"):
                if path.is_dir():
                    found.add(path)
    return sorted(found)


def _device_root() -> Path | None:
    roots = _volume_roots()
    return roots[0] if len(roots) == 1 else None


def _web_probe(timeout: float = 2) -> dict[str, Any]:
    try:
        with urllib.request.urlopen(WEB_BASE + "/", timeout=timeout) as response:
            return {"reachable": True, "status": response.status, "content_type": response.headers.get("Content-Type")}
    except Exception as exc:
        return {"reachable": False, "error": str(exc)}


def inspect_host(args: dict[str, Any]) -> dict[str, Any]:
    host = normalized_os()
    records = _serial_records()
    verified = [item for item in records if item.get("verified_picoxtools")]
    volumes = [str(path) for path in _volume_roots()]
    if host == "macos":
        usb = run(["system_profiler", "SPUSBDataType", "-detailLevel", "mini"])
        network = run(["ifconfig", "-a"])
    elif host == "linux":
        usb = run(["lsusb"])
        network = run(["ip", "addr"])
    else:
        usb = run(["powershell", "-NoProfile", "-Command", "Get-PnpDevice -PresentOnly | Format-List"])
        network = run(["ipconfig", "/all"])
    usb_lines = [line.strip() for line in usb.splitlines() if re.search(r"PicoXTools|PPVision|2e8a.*000c", line, re.I)]
    network_lines = [line.strip() for line in network.splitlines() if re.search(r"192\.168\.33\.|RNDIS|CDC-ECM|cdc_ether|usb\d|enx", line, re.I)]
    found = bool(verified or usb_lines or volumes)
    web = {"probed": False}
    if args.get("probe_web"):
        web = {"probed": True, **_web_probe()}
    return {
        "host_os": host,
        "device_evidence_found": found,
        "evidence_rule": "A generic usbmodem port alone is not PicoXtools evidence; require VID:PID 2e8a:000c, PicoXTools/PPVision identity, or PICOXTOOLS volume.",
        "verified_serial_ports": verified,
        "other_serial_ports": [item for item in records if not item.get("verified_picoxtools")],
        "usb_matches": usb_lines,
        "volumes": volumes,
        "network_matches": network_lines,
        "web_console": web,
        "interpretation": "Verified PicoXtools evidence found." if found else "No PicoXtools-specific evidence found; generic serial devices were intentionally not counted.",
    }


def _owners(port: str) -> dict[str, Any]:
    if normalized_os() == "windows":
        return {"port": port, "exists": True, "occupied": None, "processes": [], "inspection_supported": False, "error": "Process ownership inspection is not implemented for Windows COM ports; opening remains guarded by PySerial's exclusive access result."}
    if not Path(port).exists():
        return {"port": port, "exists": False, "occupied": False, "processes": []}
    output, warning = run_stdout(["lsof", port])
    if not output and warning and re.search(r"No such file|not found|timed out", warning, re.I):
        return {"port": port, "exists": True, "occupied": None, "processes": [], "inspection_supported": False, "error": warning}
    lines = [line for line in output.splitlines() if line.strip() and not line.startswith("unavailable:")]
    occupied = len(lines) > 1
    return {"port": port, "exists": True, "occupied": occupied, "processes": lines[1:] if occupied else [], "inspection_supported": True, "warning": warning, "raw": "\n".join(lines)}


def inspect_serial_owners(args: dict[str, Any]) -> dict[str, Any]:
    verified = _verified_ports()
    requested = args.get("port")
    if requested and requested not in verified:
        raise ValueError("port is not a VID:PID-verified PicoXtools CDC-UART port")
    ports = [requested] if requested else verified
    web_output, web_warning = run_stdout(["lsof", "-nP", "-iTCP@192.168.33.1"])
    web_lines = [line for line in web_output.splitlines() if line.strip()]
    return {"verified_ports": verified, "owners": [_owners(port) for port in ports], "web_connections": web_lines, "web_inspection_warning": web_warning, "note": "A browser process is only indirect evidence; Web UART ownership must still be explicitly released in the Web UI.", "writes_performed": False}


class _AssetParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.assets: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        candidate = values.get("src") if tag == "script" else values.get("href") if tag == "link" else None
        if candidate and re.search(r"\.(?:js|css)(?:\?|$)", candidate, re.I):
            self.assets.append(candidate)


def check_web_assets(args: dict[str, Any]) -> dict[str, Any]:
    timeout = min(max(float(args.get("timeout_seconds", 2)), 0.2), 5)
    try:
        with urllib.request.urlopen(WEB_BASE + "/", timeout=timeout) as response:
            html = response.read(262144).decode("utf-8", "replace")
            root_status = response.status
    except Exception as exc:
        return {"base_url": WEB_BASE, "reachable": False, "error": str(exc), "assets": [], "healthy": False}
    parser = _AssetParser()
    parser.feed(html)
    results = []
    for reference in list(dict.fromkeys(parser.assets))[:30]:
        url = urllib.parse.urljoin(WEB_BASE + "/", reference)
        if urllib.parse.urlsplit(url).netloc != "192.168.33.1":
            results.append({"reference": reference, "checked": False, "reason": "cross-origin asset rejected"})
            continue
        try:
            with urllib.request.urlopen(url, timeout=timeout) as response:
                response.read(1)
                results.append({"reference": reference, "url": url, "checked": True, "status": response.status, "ok": 200 <= response.status < 400})
        except Exception as exc:
            status = getattr(exc, "code", None)
            results.append({"reference": reference, "url": url, "checked": True, "status": status, "ok": False, "error": str(exc)})
    healthy = bool(results) and all(item.get("ok") for item in results if item.get("checked"))
    return {"base_url": WEB_BASE, "reachable": True, "root_status": root_status, "assets": results, "healthy": healthy, "note": "Root HTML alone is insufficient; all referenced same-origin JS/CSS must load."}


def list_device_files(args: dict[str, Any]) -> dict[str, Any]:
    roots = _volume_roots()
    if len(roots) != 1:
        return {"mounted": bool(roots), "volumes": [str(path) for path in roots], "files": [], "error": "Expected exactly one PICOXTOOLS volume."}
    root = roots[0]
    max_depth = min(max(int(args.get("max_depth", 3)), 1), 5)
    limit = min(max(int(args.get("limit", 200)), 1), 500)
    files = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if len(relative.parts) > max_depth or any(part.startswith("._") or part == ".fseventsd" for part in relative.parts):
            continue
        if path.is_file():
            files.append({"path": relative.as_posix(), "size": path.stat().st_size})
            if len(files) >= limit:
                break
    return {"mounted": True, "volume": str(root), "files": files, "truncated": len(files) >= limit, "writes_performed": False}


def _safe_device_path(relative: str) -> Path:
    root = _device_root()
    if root is None:
        raise ValueError("exactly one PICOXTOOLS volume must be mounted")
    if not relative or Path(relative).is_absolute():
        raise ValueError("path must be relative to the PICOXTOOLS volume")
    target = (root / relative).resolve()
    resolved_root = root.resolve()
    if target != resolved_root and resolved_root not in target.parents:
        raise ValueError("path escapes the PICOXTOOLS volume")
    return target


def read_device_file(args: dict[str, Any]) -> dict[str, Any]:
    path = _safe_device_path(args["path"])
    max_bytes = min(max(int(args.get("max_bytes", 32768)), 1), 65536)
    if not path.is_file():
        return {"path": args["path"], "exists": False, "error": "file does not exist", "writes_performed": False}
    size = path.stat().st_size
    with path.open("rb") as handle:
        raw = handle.read(max_bytes + 1)
    if b"\x00" in raw[:4096]:
        return {"path": args["path"], "exists": True, "binary": True, "size": size, "error": "binary files are not returned", "writes_performed": False}
    try:
        content = raw[:max_bytes].decode("utf-8")
    except UnicodeDecodeError:
        return {"path": args["path"], "exists": True, "binary": True, "size": size, "error": "file is not valid UTF-8", "writes_performed": False}
    return {"path": args["path"], "exists": True, "binary": False, "size": size, "truncated": size > max_bytes, "content": content, "writes_performed": False}


def preview_init_config(args: dict[str, Any]) -> dict[str, Any]:
    host = normalized_os(args.get("host_os", "auto"))
    ip = str(args.get("ip", "192.168.33.1")).strip()
    if not re.fullmatch(r"(?:\d{1,3}\.){3}\d{1,3}", ip) or any(int(part) > 255 for part in ip.split(".")):
        raise ValueError("ip must be a valid IPv4 address")
    lines = ["host=mac", f"ip={ip}"] if host == "macos" else [f"ip={ip}"]
    linux_i2c = bool(args.get("enable_linux_i2c", False))
    if linux_i2c:
        if host != "linux":
            raise ValueError("linux-i2c=1 is Linux-only and may break enumeration on Windows")
        lines.append("linux-i2c=1")
    return {
        "host_os": host,
        "filename": ".init",
        "content": "\n".join(lines) + "\n",
        "writes_performed": False,
        "warnings": ["linux-i2c=1 disables DAP-Link."] if linux_i2c else [],
        "next_step": "Read and back up the existing .init before any user-performed replacement. Never modify PicoXtools firmware.",
    }


def quick_start(args: dict[str, Any]) -> dict[str, Any]:
    host = normalized_os(args.get("host_os", "auto"))
    mode = {"macos": "CDC-ECM; .init may require host=mac", "windows": "RNDIS on Windows 10+", "linux": "CDC-ECM or RNDIS"}.get(host, "verify a documented USB network mode")
    return {
        "variant": "PicoXtools 2 screenless workflow (host-observed; no LCD assumptions)",
        "host_os": host,
        "usb_network_mode": mode,
        "steps": [
            "Call safety_policy; PicoXtools firmware is immutable and must never be upgraded, reflashed, replaced, patched, erased, or recovered.",
            "Connect with a data-capable USB-C cable.",
            "Run inspect_host; require VID:PID 2e8a:000c, PicoXTools/PPVision identity, or PICOXTOOLS volume rather than a generic usbmodem name.",
            f"Ensure the host uses {mode}.",
            "Open http://192.168.33.1 and use Web xShell 'version' for firmware identity; CDC-UART is not xShell.",
            "Before target wiring, request wiring_guide and verify 3.3 V logic/common GND.",
        ],
        "screenless_note": "Ignore LCD-dependent instructions; use USB/network/mass-storage evidence.",
    }


def wiring_guide(args: dict[str, Any]) -> dict[str, Any]:
    interface = args["interface"].lower()
    voltage = str(args.get("target_logic_voltage", "unknown"))
    target_mcu = args.get("target_mcu")
    safe = bool(re.fullmatch(r"\s*3(?:\.3)?\s*v\s*", voltage, re.I))
    data = KNOWLEDGE["wiring"][interface]
    if interface == "swd" and target_mcu and re.search(r"esp32[-_ ]?s3", target_mcu, re.I):
        safe = False
        hard_stop = "ESP32-S3 does not support SWD. PicoXtools DAPLink cannot provide ESP32-S3 hardware debugging; use UART logs or XIAO native USB Serial/JTAG."
    elif interface == "jtag":
        safe = False
        hard_stop = "No verified PicoXtools JTAG connector/probe mapping is bundled. PicoXtools DAPLink is documented as SWD-only; do not use it as an ESP32-S3 JTAG probe. Prefer XIAO native USB Serial/JTAG."
    elif safe:
        hard_stop = None
    else:
        hard_stop = "Do not connect signal wires until 3.3 V logic is verified; use an appropriate level shifter for 5 V logic."
    return {
        "interface": interface,
        "target_logic_voltage": voltage,
        "target_mcu": target_mcu,
        "safe_to_connect_directly": safe,
        "hard_stop": hard_stop,
        "sequence": ["Disconnect power while planning.", "Verify 3.3 V logic.", "Connect common GND first.", "Connect only source-backed signals.", "Power the target from its intended supply; do not connect PicoXtools 3V3 to a USB-powered target."],
        **data,
    }


def uart_diagnostic(args: dict[str, Any]) -> dict[str, Any]:
    target = args.get("target", "generic")
    wiring = ["Pico GPIO4/UART1 TX -> target RX", "target TX -> Pico GPIO5/UART1 RX", "GND -> GND", "Do not connect 3V3 when both boards are USB powered."]
    if target == "xiao-esp32s3":
        wiring = ["Pico P4/GPIO4/UART1 TX -> XIAO D7/GPIO44/RX (add only after one-way RX passes)", "XIAO D6/GPIO43/TX -> Pico P5/GPIO5/UART1 RX", "GND -> GND", "3V3 remains disconnected while both devices use USB power."]
    return {
        "target": target,
        "wiring": wiring,
        "facts": [
            "Official PPVision source defines GPIO4 as UART1 TX and GPIO5 as UART1 RX.",
            "Web UART and USB CDC share UART1; Web has priority and must be explicitly closed before CDC use.",
            "Official CDC source suspends UART polling when both DTR and RTS are false.",
            "CDC-UART is not xShell; query version through Web xShell.",
        ],
        "ordered_checks": [
            "Verify 3.3 V logic and common GND; begin with target TX -> Pico GPIO5 only.",
            "Call inspect_host and inspect_serial_owners; use only a verified VID:PID 2e8a:000c port.",
            "Close Web UART using its Close button, not only by closing a browser tab.",
            "Capture at matching 8N1 baud with DTR or RTS asserted.",
            "If empty, disconnect the target and use uart_loopback_test with GPIO4 directly wired to GPIO5.",
            "If loopback passes, restore one-way target TX -> Pico RX; add the reverse wire only after stable receive.",
        ],
        "field_result_2026_07_19": "XIAO ESP32-S3 one-way receive passed on /dev/cu.usbmodem00004 at 115200 with continuous PICO_HEARTBEAT output.",
    }


def _serial_module() -> Any:
    try:
        import serial
        return serial
    except ImportError as exc:
        raise RuntimeError("pyserial is required for UART capture/loopback; install pyserial in the MCP Python environment") from exc


def _resolve_port(requested: str | None) -> str:
    ports = _verified_ports()
    if requested:
        if requested not in ports:
            raise ValueError("requested port is not VID:PID-verified as PicoXtools")
        return requested
    if len(ports) != 1:
        raise ValueError(f"expected exactly one verified PicoXtools CDC-UART port, found {ports}")
    return ports[0]


def capture_uart(args: dict[str, Any]) -> dict[str, Any]:
    serial = _serial_module()
    port = _resolve_port(args.get("port"))
    owners = _owners(port)
    if owners["occupied"]:
        raise ValueError(f"serial port is occupied: {owners['processes']}")
    baud = int(args.get("baud", 115200))
    duration = min(max(float(args.get("duration_seconds", 5)), 0.2), 20)
    max_bytes = min(max(int(args.get("max_bytes", 65536)), 1), 65536)
    data = bytearray()
    with serial.Serial(port, baud, timeout=0.1) as handle:
        handle.dtr = bool(args.get("dtr", True))
        handle.rts = bool(args.get("rts", True))
        deadline = time.monotonic() + duration
        while time.monotonic() < deadline and len(data) < max_bytes:
            chunk = handle.read(min(4096, max_bytes - len(data)))
            if chunk:
                data.extend(chunk)
    raw = bytes(data)
    return {
        "port": port,
        "baud": baud,
        "format": "8N1",
        "dtr": bool(args.get("dtr", True)),
        "rts": bool(args.get("rts", True)),
        "duration_seconds": duration,
        "bytes_received": len(raw),
        "truncated": len(raw) >= max_bytes,
        "text": raw.decode("utf-8", "replace"),
        "hex_preview": raw[:512].hex(" "),
        "payload_transmitted": False,
    }


def uart_loopback_test(args: dict[str, Any]) -> dict[str, Any]:
    if not args.get("confirm_gpio4_to_gpio5") or not args.get("confirm_target_disconnected"):
        raise ValueError("loopback requires GPIO4->GPIO5 confirmation and confirmation that every target is disconnected")
    serial = _serial_module()
    port = _resolve_port(args.get("port"))
    owners = _owners(port)
    if owners["occupied"]:
        raise ValueError(f"serial port is occupied: {owners['processes']}")
    baud = int(args.get("baud", 115200))
    token = f"PICO_LOOPBACK_{secrets.token_hex(4).upper()}\r\n".encode("ascii")
    payload = token * 3
    received = bytearray()
    with serial.Serial(port, baud, timeout=0.1) as handle:
        handle.dtr = True
        handle.rts = True
        handle.reset_input_buffer()
        handle.write(payload)
        handle.flush()
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and len(received) < len(payload):
            chunk = handle.read(len(payload) - len(received))
            if chunk:
                received.extend(chunk)
    passed = bytes(received) == payload
    return {"port": port, "baud": baud, "frames": 3, "bytes_sent": len(payload), "bytes_received": len(received), "passed": passed, "expected_token": token.decode("ascii").strip(), "received_text": bytes(received).decode("utf-8", "replace"), "interpretation": "CDC bridge and GPIO4/TX -> GPIO5/RX loopback passed." if passed else "Loopback failed; inspect firmware/mode/wiring before reconnecting a target."}


def troubleshoot(args: dict[str, Any]) -> dict[str, Any]:
    symptom = args["symptom"].lower()
    host = normalized_os(args.get("host_os", "auto"))
    steps = ["Call safety_policy first; PicoXtools firmware must never be modified.", "Run inspect_host and require Pico-specific evidence."]
    likely: list[str] = []
    if re.search(r"192\.168\.33\.1|web|网页|控制台|404|asset|资源", symptom):
        likely += ["USB network mode mismatch", "missing/mismatched Web assets", "httpd did not start"]
        steps += [f"Verify {host} USB network mode with quick_start.", "Call check_web_assets; a 200 root page does not prove its JS/CSS exists.", "Use HTTP, not HTTPS, and temporarily bypass VPN/proxy for the local address."]
    if re.search(r"占用|busy|occupied|串口|uart|ttl|没打印|无数据|0字节|乱码", symptom):
        likely += ["wrong serial port", "Web UART still owns UART1", "DTR/RTS both false", "baud/wiring mismatch"]
        steps += ["Call inspect_serial_owners; use the verified CDC-UART port only.", "Explicitly close Web UART before CDC capture.", "Use uart_diagnostic and capture_uart at matching 8N1 baud.", "If capture is empty, disconnect the target and run the guarded GPIO4->GPIO5 loopback."]
    if re.search(r"usb|识别|枚举|volume|磁盘", symptom):
        likely += ["charge-only cable", "host-mode mismatch", "USB enumeration issue"]
        steps += ["Try a known data cable/direct port.", "Check VID:PID 2e8a:000c and the PICOXTOOLS volume.", "On Windows use Windows 10+; linux-i2c=1 can conflict with expected interfaces."]
    if re.search(r"dap|swd|keil|烧录|调试", symptom):
        likely += ["DAP transport/version mismatch", "SWD wiring/power issue", "linux-i2c disabled DAP-Link"]
        steps += ["Verify SWDIO/SWCLK/common GND and target power.", "Use CMSIS-DAP 2.1+ and Keil 5.36+ where applicable.", "Disable linux-i2c when DAP-Link is required."]
    if len(steps) == 2:
        steps += ["Call search_docs with the exact feature/error.", "Verify voltage, common GND, cable, and host mode before any target-firmware change; PicoXtools firmware changes are forbidden."]
    return {"symptom": args["symptom"], "host_os": host, "likely_causes": list(dict.fromkeys(likely)), "steps": steps, "do_not_do": ["Never upgrade, reflash, replace, patch, erase, downgrade, or recover PicoXtools firmware.", "Do not connect 5 V logic directly.", "Do not confuse target-MCU flashing with PicoXtools firmware modification."]}


def search_docs(args: dict[str, Any]) -> dict[str, Any]:
    query = args["query"].strip().lower()
    aliases = KNOWLEDGE.get("aliases", {})
    expanded = query + " " + " ".join(value for key, value in aliases.items() if key in query)
    latin = re.findall(r"[a-z0-9_.+-]+", expanded)
    chinese_phrases = re.findall(r"[\u4e00-\u9fff]{2,}", expanded)
    tokens = list(dict.fromkeys(latin + chinese_phrases + ([query] if query else [])))
    scored = []
    for entry in KNOWLEDGE["entries"]:
        topic = entry["topic"].lower()
        haystack = (topic + " " + entry["text"]).lower()
        score = sum(4 if token in topic else 1 for token in tokens if token and token in haystack)
        if query and query in haystack:
            score += 6
        if score:
            scored.append((score, entry))
    scored.sort(key=lambda item: (-item[0], item[1]["topic"]))
    limit = min(max(int(args.get("limit", 5)), 1), 10)
    return {"query": args["query"], "expanded_query": expanded, "matches": [entry for _, entry in scored[:limit]], "note": "No bundled match; consult the official guide and PPVision source." if not scored else None}


HANDLERS = {
    "safety_policy": safety_policy,
    "inspect_host": inspect_host,
    "inspect_serial_owners": inspect_serial_owners,
    "check_web_assets": check_web_assets,
    "list_device_files": list_device_files,
    "read_device_file": read_device_file,
    "preview_init_config": preview_init_config,
    "quick_start": quick_start,
    "wiring_guide": wiring_guide,
    "uart_diagnostic": uart_diagnostic,
    "capture_uart": capture_uart,
    "uart_loopback_test": uart_loopback_test,
    "troubleshoot": troubleshoot,
    "search_docs": search_docs,
}


def reply(request_id: Any, result: Any = None, error: Any = None) -> None:
    message: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id}
    message["error" if error is not None else "result"] = error if error is not None else result
    sys.stdout.write(json.dumps(message, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _tool_result(value: Any, is_error: bool = False) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False, indent=2)}],
        "structuredContent": value,
        "isError": is_error,
    }


def main() -> None:
    for raw in sys.stdin:
        if not raw.strip():
            continue
        request: dict[str, Any] | None = None
        try:
            request = json.loads(raw)
            method = request.get("method")
            request_id = request.get("id")
            if method == "initialize":
                reply(request_id, {"protocolVersion": "2024-11-05", "capabilities": {"tools": {"listChanged": False}}, "serverInfo": {"name": "picoxtools-debugger", "version": SERVER_VERSION}})
            elif method == "ping":
                reply(request_id, {})
            elif method == "tools/list":
                reply(request_id, {"tools": TOOLS})
            elif method == "tools/call":
                params = request.get("params", {})
                name = params.get("name")
                if name not in HANDLERS:
                    reply(request_id, error={"code": -32601, "message": f"Unknown tool: {name}"})
                    continue
                try:
                    arguments = params.get("arguments", {})
                    if not isinstance(arguments, dict):
                        raise ValueError("tool arguments must be an object")
                    reply(request_id, _tool_result(HANDLERS[name](arguments)))
                except Exception as exc:
                    reply(request_id, _tool_result({"error": str(exc), "tool": name}, is_error=True))
            elif method and request_id is not None:
                reply(request_id, error={"code": -32601, "message": f"Method not found: {method}"})
        except json.JSONDecodeError as exc:
            reply(None, error={"code": -32700, "message": str(exc)})
        except Exception as exc:
            request_id = request.get("id") if isinstance(request, dict) else None
            reply(request_id, error={"code": -32603, "message": str(exc)})


if __name__ == "__main__":
    main()
