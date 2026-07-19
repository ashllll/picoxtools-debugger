from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib


ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


server = load_module("pico_server_for_reasonix", ROOT / "mcp" / "server.py")
installer = load_module("pico_reasonix_installer", ROOT / "scripts" / "configure_reasonix.py")


class ReasonixProjectConfigTests(unittest.TestCase):
    def test_project_config_registers_skill_and_mcp(self) -> None:
        config = tomllib.loads((ROOT / "reasonix.toml").read_text(encoding="utf-8"))
        self.assertEqual(config["skills"]["paths"], ["./skills"])
        plugin = next(item for item in config["plugins"] if item["name"] == "picoxtools-debugger")
        self.assertEqual(plugin["command"], "python3")
        self.assertEqual(plugin["args"], ["mcp/server.py"])

    def test_trust_list_exactly_matches_mcp_read_only_annotations(self) -> None:
        config = tomllib.loads((ROOT / "reasonix.toml").read_text(encoding="utf-8"))
        plugin = next(item for item in config["plugins"] if item["name"] == "picoxtools-debugger")
        annotated = {tool["name"] for tool in server.TOOLS if tool["annotations"]["readOnlyHint"]}
        trusted = set(plugin["trusted_read_only_tools"])
        self.assertEqual(trusted, annotated)
        self.assertNotIn("capture_uart", trusted)
        self.assertNotIn("uart_loopback_test", trusted)


class ReasonixInstallerTests(unittest.TestCase):
    def test_multiple_configs_require_explicit_selection(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            legacy = home / ".reasonix" / "config.toml"
            modern = home / "Library" / "Application Support" / "reasonix" / "config.toml"
            legacy.parent.mkdir(parents=True)
            modern.parent.mkdir(parents=True)
            legacy.write_text("config_version = 3\n", encoding="utf-8")
            modern.write_text("config_version = 3\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "multiple Reasonix configs"):
                installer.resolve_config_path(None, home)

    def test_merge_preserves_unrelated_config_and_is_idempotent(self) -> None:
        original = """default_model = \"example\"

[skills]
paths = [\"/tmp/another-skill-root\"]

[[plugins]]
name = \"unrelated\"
command = \"other-server\"
"""
        python = Path("/usr/bin/python3")
        updated, changes = installer.compose_config(original, ROOT, python)
        parsed = tomllib.loads(updated)
        self.assertEqual(parsed["default_model"], "example")
        self.assertIn("/tmp/another-skill-root", parsed["skills"]["paths"])
        self.assertIn(str((ROOT / "skills").resolve()), parsed["skills"]["paths"])
        self.assertIn("skill path", changes)
        plugins = {item["name"]: item for item in parsed["plugins"]}
        self.assertEqual(plugins["unrelated"]["command"], "other-server")
        self.assertEqual(plugins["picoxtools-debugger"]["command"], str(python.resolve()))
        second, second_changes = installer.compose_config(updated, ROOT, python)
        self.assertEqual(second, updated)
        self.assertEqual(second_changes, [])

    def test_existing_plugin_is_replaced_without_duplication(self) -> None:
        original = """[skills]
paths = []

[[plugins]]
name = \"picoxtools-debugger\"
command = \"stale-python\"
args = [\"stale-server.py\"]
trusted_read_only_tools = [\"inspect_host\"]

[[plugins]]
name = \"keep-me\"
command = \"keep-server\"

[after]
preserved = true
"""
        updated, _ = installer.compose_config(original, ROOT, Path("/usr/bin/python3"))
        parsed = tomllib.loads(updated)
        plugins = [item for item in parsed["plugins"] if item["name"] == "picoxtools-debugger"]
        self.assertEqual(len(plugins), 1)
        self.assertEqual(plugins[0]["trusted_read_only_tools"], installer.TRUSTED_READ_ONLY_TOOLS)
        self.assertTrue(any(item["name"] == "keep-me" for item in parsed["plugins"]))
        self.assertTrue(parsed["after"]["preserved"])

    def test_atomic_write_creates_backup(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "config.toml"
            path.write_text('default_model = "old"\n', encoding="utf-8")
            backup = installer.atomic_write_with_backup(path, 'default_model = "new"\n')
            self.assertIsNotNone(backup)
            self.assertEqual(path.read_text(encoding="utf-8"), 'default_model = "new"\n')
            self.assertEqual(backup.read_text(encoding="utf-8"), 'default_model = "old"\n')


if __name__ == "__main__":
    unittest.main()
