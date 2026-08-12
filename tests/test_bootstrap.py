import json
import importlib.util
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).parents[1]


class BootstrapTest(unittest.TestCase):
    def test_dry_run_is_repeatable_and_plans_both_managed_installers(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            command = [sys.executable, str(ROOT / "scripts/bootstrap.py"),
                       "--codex-home", str(root / "codex"), "--tools-home", str(root / "tools"),
                       "--repo", str(ROOT), "--dry-run"]
            first = json.loads(subprocess.check_output(command, text=True))
            second = json.loads(subprocess.check_output(command, text=True))
            self.assertEqual(first, second)
            self.assertIn(first["status"], {"READY", "PARTIAL"})
            self.assertEqual(len(first["actions"]), 2)
            self.assertTrue(first["dependencies"]["truthful"])
            self.assertTrue(first["dependencies"]["commands"])
            self.assertFalse((root / "codex").exists())
            self.assertFalse((root / "tools").exists())

    def test_dependency_route_is_executable_guidance_not_implicit_mutation(self):
        spec = importlib.util.spec_from_file_location(
            "bootstrap", ROOT / "scripts/bootstrap.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        result = module.dependency_plan()
        self.assertIn("managed_route", result)
        self.assertTrue(result["truthful"])
        self.assertNotIn("pyright", result["missing"])
        self.assertEqual(result["tools"]["pyright"]["status"], "MANAGED")

    def _load(self):
        spec = importlib.util.spec_from_file_location("bootstrap", ROOT / "scripts/bootstrap.py")
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        return module

    def test_install_system_deps_stops_on_failure_and_reports_not_ready(self):
        module = self._load()
        plan = {"commands": [["pkg", "one"], ["pkg", "two"]], "missing": ["clangd"],
                "tools": {}, "managed": ["pyright"], "host_required": ["clangd"],
                "managed_route": [], "install_opt_in": "--install-system-deps",
                "system": "test", "truthful": True}
        ready_plan = dict(plan, missing=[])
        actions = []
        def fake_run(command):
            actions.append(command)
            return {"command": command, "returncode": 17 if command == ["pkg", "one"] else 0, "output": "fail"}
        with mock.patch.object(module, "dependency_plan", side_effect=[plan, ready_plan]), \
             mock.patch.object(module, "_run", side_effect=fake_run):
            with mock.patch("sys.stdout") as stdout:
                self.assertEqual(module.main(["--install-system-deps"]), 2)
        self.assertEqual(actions, [["pkg", "one"],
                                   mock.ANY, mock.ANY])

    def test_install_system_deps_reprobes_and_ready_without_host_actions_when_ready(self):
        module = self._load()
        ready = {"commands": [["pkg", "one"]], "missing": [], "tools": {},
                 "managed": ["pyright"], "host_required": ["clangd"],
                 "managed_route": [], "install_opt_in": "--install-system-deps",
                 "system": "test", "truthful": True}
        actions = []
        with mock.patch.object(module, "dependency_plan", return_value=ready), \
             mock.patch.object(module, "_run", side_effect=lambda command: actions.append(command) or
                               {"command": command, "returncode": 0, "output": ""}):
            with mock.patch("sys.stdout") as stdout:
                self.assertEqual(module.main(["--install-system-deps"]), 0)
        self.assertEqual(len(actions), 2)  # governance and semantic only
        self.assertNotIn(["pkg", "one"], actions)

    def test_default_managed_roots_are_safe_codex_paths(self):
        module = self._load()
        parser = module.argparse.ArgumentParser()
        parser.add_argument("--codex-home", default=str(pathlib.Path.home() / ".codex"))
        parser.add_argument("--tools-home", default=str(pathlib.Path.home() / ".codex" / "semantic-tools"))
        args = parser.parse_args([])
        self.assertEqual(args.codex_home, str(pathlib.Path.home() / ".codex"))
        self.assertEqual(args.tools_home, str(pathlib.Path.home() / ".codex" / "semantic-tools"))

    def test_dependency_plans_are_platform_specific(self):
        spec = importlib.util.spec_from_file_location("bootstrap", ROOT / "scripts/bootstrap.py")
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        with mock.patch.object(module.platform, "system", return_value="Darwin"):
            self.assertEqual(module.dependency_plan()["system"], "darwin")
            self.assertTrue(any("brew" == command[0] for command in module.dependency_plan()["commands"]))
        with mock.patch.object(module.platform, "system", return_value="Windows"):
            self.assertEqual(module.dependency_plan()["system"], "windows")
            self.assertTrue(any("winget" == command[0] for command in module.dependency_plan()["commands"]))


if __name__ == "__main__":
    unittest.main()
