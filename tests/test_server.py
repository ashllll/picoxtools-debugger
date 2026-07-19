from __future__ import annotations

import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SERVER_PATH = PLUGIN_ROOT / "mcp" / "server.py"
SPEC = importlib.util.spec_from_file_location("picoxtools_mcp_server", SERVER_PATH)
assert SPEC and SPEC.loader
server = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(server)


class FakeResponse:
    def __init__(self, body: bytes, status: int = 200, content_type: str = "text/html") -> None:
        self.body = body
        self.status = status
        self.headers = {"Content-Type": content_type}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, size: int = -1) -> bytes:
        if size < 0:
            return self.body
        return self.body[:size]


class FakeSerialHandle:
    def __init__(self, *_args, **_kwargs) -> None:
        self.dtr = False
        self.rts = False
        self.pending = bytearray(b"PICO_HEARTBEAT count=1\r\n")
        self.written = b""

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, size: int) -> bytes:
        if self.written:
            result = self.written[:size]
            self.written = self.written[size:]
            return result
        result = bytes(self.pending[:size])
        del self.pending[:size]
        return result

    def write(self, payload: bytes) -> int:
        self.written += payload
        return len(payload)

    def flush(self) -> None:
        pass

    def reset_input_buffer(self) -> None:
        self.pending.clear()


class FakeSerialModule:
    Serial = FakeSerialHandle


class SafetyTests(unittest.TestCase):
    def test_firmware_policy_is_absolute_and_target_flash_is_separate(self) -> None:
        result = server.safety_policy({})
        self.assertTrue(result["picoxtools_firmware_immutable"])
        self.assertIn("任何条件下都不允许被修改", result["rule_zh"])
        self.assertIn("Target-MCU flashing", result["allowed"][-1])

    def test_no_pico_firmware_write_tool_exists(self) -> None:
        names = {tool["name"] for tool in server.TOOLS}
        forbidden = {"upgrade_firmware", "flash_picoxtools", "write_firmware", "copy_uf2", "recover_firmware", "erase_firmware"}
        self.assertTrue(names.isdisjoint(forbidden))

    def test_uart_annotations_are_not_read_only(self) -> None:
        tools = {tool["name"]: tool for tool in server.TOOLS}
        self.assertFalse(tools["capture_uart"]["annotations"]["readOnlyHint"])
        self.assertFalse(tools["uart_loopback_test"]["annotations"]["readOnlyHint"])


class DiscoveryTests(unittest.TestCase):
    @mock.patch.object(server, "run", return_value="")
    @mock.patch.object(server, "_volume_roots", return_value=[])
    @mock.patch.object(server, "_serial_records")
    def test_generic_usbmodem_does_not_count_as_pico(self, serial_records, _volumes, _run) -> None:
        serial_records.return_value = [{"port": "/dev/cu.usbmodem123", "verified_picoxtools": False}]
        result = server.inspect_host({})
        self.assertFalse(result["device_evidence_found"])
        self.assertEqual(result["verified_serial_ports"], [])

    @mock.patch.object(server, "run", return_value="")
    @mock.patch.object(server, "_volume_roots", return_value=[])
    @mock.patch.object(server, "_serial_records")
    def test_vid_pid_verified_port_counts(self, serial_records, _volumes, _run) -> None:
        record = {"port": "/dev/cu.usbmodem00004", "vid": "2e8a", "pid": "000c", "verified_picoxtools": True}
        serial_records.return_value = [record]
        result = server.inspect_host({})
        self.assertTrue(result["device_evidence_found"])
        self.assertEqual(result["verified_serial_ports"], [record])

    @mock.patch.object(server, "_verified_ports", return_value=["/dev/cu.pico"])
    @mock.patch.object(server, "_owners", return_value={"port": "/dev/cu.pico", "occupied": False})
    @mock.patch.object(server, "run_stdout", return_value=("", None))
    def test_serial_owner_inspection_does_not_mutate(self, _run, _owners, _ports) -> None:
        result = server.inspect_serial_owners({})
        self.assertFalse(result["writes_performed"])
        self.assertEqual(result["verified_ports"], ["/dev/cu.pico"])

    def test_lsof_stderr_warning_is_not_false_occupancy(self) -> None:
        with tempfile.NamedTemporaryFile() as handle:
            with mock.patch.object(server, "run_stdout", return_value=("", "lsof: WARNING: unrelated mounted volume")):
                result = server._owners(handle.name)
        self.assertFalse(result["occupied"])
        self.assertEqual(result["processes"], [])

    @mock.patch.object(server, "normalized_os", return_value="windows")
    def test_windows_com_owner_status_is_unknown_not_free(self, _host) -> None:
        result = server._owners("COM7")
        self.assertIsNone(result["occupied"])
        self.assertFalse(result["inspection_supported"])


class VolumeTests(unittest.TestCase):
    def test_read_text_and_reject_binary_and_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "ok.txt").write_text("hello", encoding="utf-8")
            (root / "binary.bin").write_bytes(b"a\x00b")
            with mock.patch.dict(os.environ, {"PICOXTOOLS_DEVICE_ROOT": temporary}):
                self.assertEqual(server.read_device_file({"path": "ok.txt"})["content"], "hello")
                self.assertTrue(server.read_device_file({"path": "binary.bin"})["binary"])
                with self.assertRaisesRegex(ValueError, "escapes"):
                    server.read_device_file({"path": "../outside"})

    def test_utf8_truncation_is_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "large.txt").write_text("abcdef", encoding="utf-8")
            with mock.patch.dict(os.environ, {"PICOXTOOLS_DEVICE_ROOT": temporary}):
                result = server.read_device_file({"path": "large.txt", "max_bytes": 3})
                self.assertEqual(result["content"], "abc")
                self.assertTrue(result["truncated"])


class GuidanceTests(unittest.TestCase):
    def test_uart_direction_is_explicit(self) -> None:
        result = server.wiring_guide({"interface": "uart", "target_logic_voltage": "3.3V"})
        self.assertIn("Pico GPIO4 / UART1 TX", result["pins"])
        self.assertIn("Pico GPIO5 / UART1 RX", result["pins"])
        self.assertTrue(result["safe_to_connect_directly"])

    def test_esp32_s3_swd_is_rejected(self) -> None:
        result = server.wiring_guide({"interface": "swd", "target_logic_voltage": "3.3V", "target_mcu": "ESP32-S3"})
        self.assertFalse(result["safe_to_connect_directly"])
        self.assertIn("does not support SWD", result["hard_stop"])

    def test_jtag_is_not_claimed(self) -> None:
        result = server.wiring_guide({"interface": "jtag", "target_logic_voltage": "3.3V", "target_mcu": "ESP32-S3"})
        self.assertFalse(result["safe_to_connect_directly"])
        self.assertIn("SWD-only", result["hard_stop"])

    def test_linux_i2c_rejected_for_macos(self) -> None:
        with self.assertRaisesRegex(ValueError, "Linux-only"):
            server.preview_init_config({"host_os": "macos", "enable_linux_i2c": True})

    def test_chinese_doc_alias_search(self) -> None:
        result = server.search_docs({"query": "串口没打印"})
        topics = [entry["topic"] for entry in result["matches"]]
        self.assertTrue(any("UART" in topic or "CDC" in topic for topic in topics))


class WebTests(unittest.TestCase):
    def test_assets_are_checked_independently(self) -> None:
        html = b'<html><script src="/assets/app.js"></script><link rel="stylesheet" href="/assets/app.css"></html>'

        def fake_urlopen(url, timeout):
            value = getattr(url, "full_url", url)
            if value.endswith("/"):
                return FakeResponse(html)
            if value.endswith("app.js"):
                return FakeResponse(b"x", content_type="application/javascript")
            raise OSError("404 missing css")

        with mock.patch.object(server.urllib.request, "urlopen", side_effect=fake_urlopen):
            result = server.check_web_assets({})
        self.assertTrue(result["reachable"])
        self.assertFalse(result["healthy"])
        self.assertEqual(len(result["assets"]), 2)


class UartTests(unittest.TestCase):
    @mock.patch.object(server, "_serial_module", return_value=FakeSerialModule())
    @mock.patch.object(server, "_resolve_port", return_value="/dev/cu.pico")
    @mock.patch.object(server, "_owners", return_value={"occupied": False, "processes": []})
    def test_capture_is_bounded_and_sends_no_payload(self, _owners, _port, _serial) -> None:
        with mock.patch.object(server.time, "monotonic", side_effect=[0.0, 0.1, 0.3]):
            result = server.capture_uart({"duration_seconds": 0.2})
        self.assertGreater(result["bytes_received"], 0)
        self.assertIn("PICO_HEARTBEAT", result["text"])
        self.assertFalse(result["payload_transmitted"])

    def test_loopback_requires_both_confirmations(self) -> None:
        with self.assertRaisesRegex(ValueError, "requires"):
            server.uart_loopback_test({"confirm_gpio4_to_gpio5": True, "confirm_target_disconnected": False})

    @mock.patch.object(server, "_serial_module", return_value=FakeSerialModule())
    @mock.patch.object(server, "_resolve_port", return_value="/dev/cu.pico")
    @mock.patch.object(server, "_owners", return_value={"occupied": False, "processes": []})
    def test_confirmed_loopback_passes(self, _owners, _port, _serial) -> None:
        result = server.uart_loopback_test({"confirm_gpio4_to_gpio5": True, "confirm_target_disconnected": True})
        self.assertTrue(result["passed"])
        self.assertEqual(result["bytes_sent"], result["bytes_received"])


class ProtocolTests(unittest.TestCase):
    def test_json_rpc_initialize_list_and_call(self) -> None:
        requests = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "safety_policy", "arguments": {}}},
        ]
        process = subprocess.run(
            [sys.executable, str(SERVER_PATH)],
            input="".join(json.dumps(item) + "\n" for item in requests),
            text=True,
            capture_output=True,
            timeout=10,
            check=True,
        )
        responses = [json.loads(line) for line in process.stdout.splitlines()]
        self.assertEqual(responses[0]["result"]["serverInfo"]["version"], server.SERVER_VERSION)
        self.assertGreaterEqual(len(responses[1]["result"]["tools"]), 10)
        self.assertTrue(responses[2]["result"]["structuredContent"]["picoxtools_firmware_immutable"])


if __name__ == "__main__":
    unittest.main()
