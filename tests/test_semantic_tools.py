import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
import importlib.util

ROOT = pathlib.Path(__file__).parents[1]

_LAUNCHER_SPEC = importlib.util.spec_from_file_location(
    "semantic_backend_launcher", ROOT / "codex/bin/semantic-backend-launcher.py"
)
semantic_backend_launcher = importlib.util.module_from_spec(_LAUNCHER_SPEC)
_LAUNCHER_SPEC.loader.exec_module(semantic_backend_launcher)


class SemanticToolsTest(unittest.TestCase):
    def test_run_accepts_build_environment_for_ttsc_go_plugin(self):
        spec = importlib.util.spec_from_file_location(
            "semantic_tools_installer_run", ROOT / "scripts/install-semantic-tools.py")
        installer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(installer)
        env = dict(os.environ, GOFLAGS="-buildvcs=false", V21_ENV_PROBE="present")
        code, output = installer._run(
            [sys.executable, "-c", "import os; print(os.environ['V21_ENV_PROBE'])"], env=env)
        self.assertEqual(code, 0)
        self.assertEqual(output, "present")

    def test_cold_build_uses_offline_resource_profile(self):
        spec = importlib.util.spec_from_file_location(
            "semantic_tools_installer_build", ROOT / "scripts/install-semantic-tools.py")
        installer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(installer)
        command = installer._bounded_build_command(["pnpm", "run", "build"])
        self.assertIn("semantic-backend-launcher.py", command[1])
        self.assertEqual(command[command.index("--profile") + 1], "cpp_offline")
        self.assertEqual(command[-3:], ["pnpm", "run", "build"])

    def test_backend_selects_python_langserver_for_python_workset(self):
        spec = importlib.util.spec_from_file_location(
            "semantic_tools_installer_lane", ROOT / "scripts/install-semantic-tools.py")
        installer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(installer)
        with tempfile.TemporaryDirectory() as directory:
            tools_home = pathlib.Path(directory)
            entrypoint = tools_home / "bin.js"
            command = installer._backend_command(tools_home, entrypoint, tools_home, ("module.py",))
            self.assertEqual(command[command.index("--profile") + 1], "python_resident")
            self.assertIn("python", command)
            self.assertIn(str(tools_home / "pyright/bin/pyright-langserver"), command)
            self.assertIn("--stdio", command)

    def test_dry_run_is_idempotent_and_does_not_write(self):
        with tempfile.TemporaryDirectory() as directory:
            home = pathlib.Path(directory) / "tools"
            command = [sys.executable, str(ROOT / "scripts/install-semantic-tools.py"),
                       "--tools-home", str(home), "--install", "--dry-run"]
            first = json.loads(subprocess.check_output(command, text=True))
            second = json.loads(subprocess.check_output(command, text=True))
            self.assertEqual(first, second)
            self.assertEqual(first["operation"], "DRY_RUN")
            self.assertFalse(home.exists())
            self.assertEqual(first["upstream"]["head"], "95e20c9540e85fef542466172484229356d3d0d8")
            self.assertEqual(first["upstream"]["tree"], "e9ce033e380d77265c601579e436218502a6ccbd")

    def test_doctor_reports_partial_without_faking_ready(self):
        with tempfile.TemporaryDirectory() as directory:
            result = json.loads(subprocess.check_output([
                sys.executable, str(ROOT / "scripts/install-semantic-tools.py"),
                "--tools-home", str(pathlib.Path(directory) / "tools"), "--doctor"], text=True))
            self.assertIn(result["status"], {"PARTIAL", "READY"})
            self.assertTrue(result["truthful"])
            if result["status"] != "READY":
                self.assertEqual(result["fallback"], "bounded_exact_evidence")

    def test_dry_run_plans_clone_build_provider_launcher_and_registration(self):
        with tempfile.TemporaryDirectory() as directory:
            tools_home = pathlib.Path(directory) / "tools"
            codex_home = pathlib.Path(directory) / "codex"
            result = json.loads(subprocess.check_output([
                sys.executable, str(ROOT / "scripts/install-semantic-tools.py"),
                "--tools-home", str(tools_home), "--codex-home", str(codex_home),
                "--install", "--register", "--dry-run"], text=True))
            self.assertEqual(result["operation"], "DRY_RUN")
            self.assertEqual(result["planned"]["clone"]["checkout"], "95e20c9540e85fef542466172484229356d3d0d8")
            self.assertIn("pnpm install --frozen-lockfile", result["planned"]["build"])
            self.assertIn("pnpm run build", result["planned"]["build"])
            self.assertIn("pyright==", result["planned"]["pyright"])
            self.assertIn("semantic-backend-launcher.py", result["planned"]["launcher"])
            self.assertIn("semantic-gateway-mcp.json", result["planned"]["register"]["json"])
            self.assertFalse(tools_home.exists())

    def test_resource_command_is_bounded(self):
        command = semantic_backend_launcher.build_limited_command(
            ["node", "inspect.js"], profile="cpp_resident", use_systemd=True, timeout_sec=180)
        self.assertEqual(command[:3], ["systemd-run", "--user", "--quiet"])
        self.assertIn("--wait", command)
        self.assertIn("--pipe", command)
        self.assertIn("--collect", command)
        self.assertIn("--same-dir", command)
        self.assertNotIn("--scope", command)
        self.assertIn("CPUQuota=400%", command)
        self.assertIn("MemoryMax=4G", command)
        self.assertIn("RuntimeMaxSec=180s", command)
        self.assertIn("--setenv", command)
        self.assertTrue(any(item.startswith("PATH=") for item in command))

    def test_launcher_executes_compatible_systemd_transient_service_arguments(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            record = root / "argv.json"
            fake = root / "systemd-run"
            fake.write_text(
                "#!/usr/bin/env python3\n"
                "import json, os, pathlib, sys\n"
                "pathlib.Path(os.environ['LAUNCHER_ARGV']).write_text(json.dumps(sys.argv[1:]))\n",
                encoding="utf-8")
            fake.chmod(0o700)
            env = dict(os.environ, PATH=str(root) + ":" + os.environ["PATH"],
                       LAUNCHER_ARGV=str(record))
            completed = subprocess.run([
                sys.executable, str(ROOT / "codex/bin/semantic-backend-launcher.py"),
                "--profile", "cpp_resident", "--", sys.executable, "-c", "pass",
            ], env=env, check=False)
            self.assertEqual(completed.returncode, 0)
            argv = json.loads(record.read_text(encoding="utf-8"))
            self.assertIn("--wait", argv)
            self.assertIn("--pipe", argv)
            self.assertNotIn("--scope", argv)
            self.assertIn("CPUQuota=400%", argv)

    def test_registration_preserves_unrelated_config_and_uninstall_restores_it(self):
        spec = importlib.util.spec_from_file_location(
            "semantic_tools_installer", ROOT / "scripts/install-semantic-tools.py")
        installer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(installer)
        with tempfile.TemporaryDirectory() as directory:
            codex_home = pathlib.Path(directory) / "codex"
            tools_home = pathlib.Path(directory) / "tools"
            codex_home.mkdir(); tools_home.mkdir()
            config = codex_home / "config.toml"
            original = "model = \"preserve\"\n\n[mcp_servers.other]\ncommand = \"other\"\n"
            config.write_text(original, encoding="utf-8")
            installer._write_registration(codex_home, tools_home)
            installer._upsert_mcp_config(codex_home, tools_home)
            first_registration = json.loads((codex_home / installer.REGISTRATION).read_text(encoding="utf-8"))
            self.assertEqual(first_registration["args"], ["--config", str(tools_home / "semantic-gateway-config.json")])
            registered = config.read_text(encoding="utf-8")
            self.assertIn("[mcp_servers.other]", registered)
            self.assertIn("[mcp_servers.codex-semantic-gateway]", registered)
            self.assertNotEqual(registered, original)
            # A repeated registration must retain the first backup, not back up
            # the already-managed config over the original bytes.
            installer._write_registration(codex_home, tools_home)
            installer._upsert_mcp_config(codex_home, tools_home)
            removed = installer._remove_mcp_config(codex_home)
            self.assertIn(installer.CONFIG_BACKUP, removed)
            self.assertEqual(config.read_text(encoding="utf-8"), original)
            self.assertNotIn("[mcp_servers.codex-semantic-gateway]", config.read_text(encoding="utf-8"))

    def test_clean_home_double_registration_leaves_config_absent_after_uninstall(self):
        spec = importlib.util.spec_from_file_location(
            "semantic_tools_clean_registration", ROOT / "scripts/install-semantic-tools.py")
        installer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(installer)
        with tempfile.TemporaryDirectory() as directory:
            codex_home = pathlib.Path(directory) / "codex"
            tools_home = pathlib.Path(directory) / "tools"
            codex_home.mkdir(); tools_home.mkdir()
            config = codex_home / installer.CONFIG_TOML
            self.assertFalse(config.exists())
            installer._write_registration(codex_home, tools_home)
            installer._upsert_mcp_config(codex_home, tools_home)
            installer._write_registration(codex_home, tools_home)
            installer._upsert_mcp_config(codex_home, tools_home)
            self.assertFalse((codex_home / installer.CONFIG_BACKUP).exists())
            installer._remove_mcp_config(codex_home)
            self.assertFalse(config.exists())

    def test_doctor_workset_is_derived_and_capped_or_explicit(self):
        spec = importlib.util.spec_from_file_location(
            "semantic_tools_workset", ROOT / "scripts/install-semantic-tools.py")
        installer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(installer)
        with tempfile.TemporaryDirectory() as directory:
            repo = pathlib.Path(directory)
            subprocess.check_call(["git", "init", "-q"], cwd=repo)
            for index in range(70):
                (repo / f"unit_{index:02d}.cpp").write_text("int x;\n", encoding="utf-8")
            subprocess.check_call(["git", "add", "."], cwd=repo)
            derived = installer._derive_workset(repo)
            self.assertEqual(len(derived), 64)
            self.assertEqual(derived, tuple(sorted(derived)))
            explicit = installer._derive_workset(repo, ("unit_69.cpp",))
            self.assertEqual(explicit, ("unit_69.cpp",))


if __name__ == "__main__":
    unittest.main()
