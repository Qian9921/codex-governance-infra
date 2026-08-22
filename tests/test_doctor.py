from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
import subprocess
import sys

from scripts.doctor import doctor
from scripts.install import install


ROOT = Path(__file__).resolve().parents[1]


class DoctorTests(unittest.TestCase):
    def test_direct_script_entrypoint(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(ROOT / "scripts/doctor.py"), "--help"],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_blank_home_reports_effective_profiles_and_project_rules(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            local = root / "local.toml"
            local.write_text(
                """
[models]
primary = "primary-model"
primary_effort = "medium"
executor = "executor-model"
executor_effort = "medium"
reviewer = "reviewer-model"

[opening]
instruction = "Local-only opening."

[tools]
codegraph = ""
semble = ""
rtk = ""
""".lstrip(),
                encoding="utf-8",
            )
            codex_home = root / "codex"
            install(ROOT, codex_home, local, root / "state")

            report = doctor(codex_home, local, ROOT / "tests", check_github=False)
            checks = {check["name"]: check for check in report["checks"]}
            self.assertTrue(report["ok"])
            self.assertTrue(checks["codex_config_syntax"]["ok"])
            self.assertTrue(checks["primary_profile"]["ok"])
            self.assertTrue(checks["agent_v23_executor"]["ok"])
            self.assertTrue(checks["agent_v23_reviewer"]["ok"])
            self.assertEqual(report["project_instruction_candidates"], [str(ROOT / "AGENTS.md")])
            self.assertEqual(report["primary_profile_start"], "codex --profile v23-primary")


if __name__ == "__main__":
    unittest.main()
