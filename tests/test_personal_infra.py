import json
import pathlib
import re
import unittest


ROOT = pathlib.Path(__file__).parents[1]


def quoted_scalar(document, key):
    match = re.search(rf'^\s*{re.escape(key)}\s*=\s*"([^"]*)"\s*$', document, re.MULTILINE)
    if match is None:
        raise AssertionError(f"missing string setting: {key}")
    return match.group(1)


class PersonalInfra(unittest.TestCase):
    def test_personal_agents_are_native_and_single_role(self):
        expected = {
            "luna-execution.toml": ("luna_execution", "gpt-5.6-luna"),
            "sol-contract.toml": ("sol_contract", "gpt-5.6-sol"),
            "sol-reviewer.toml": ("sol_reviewer", "gpt-5.6-sol"),
            "terra-triage.toml": ("terra_triage", "gpt-5.6-terra"),
        }
        actual = {path.name: path for path in (ROOT / "codex" / "agents").glob("*.toml")}
        self.assertEqual(set(actual), set(expected))
        for filename, (name, model) in expected.items():
            value = actual[filename].read_text(encoding="utf-8")
            self.assertEqual(quoted_scalar(value, "name"), name)
            self.assertEqual(quoted_scalar(value, "model"), model)
            self.assertTrue(quoted_scalar(value, "description"))
            self.assertIn('developer_instructions = """\n', value)
            self.assertTrue(value.rstrip().endswith('"""'))
            if name != "luna_execution":
                self.assertEqual(quoted_scalar(value, "sandbox_mode"), "read-only")
            if name == "sol_contract":
                self.assertEqual(quoted_scalar(value, "model_reasoning_effort"), "medium")
                self.assertNotIn("max_output_tokens", value)
            if name == "sol_reviewer":
                self.assertEqual(quoted_scalar(value, "model_reasoning_effort"), "high")
                self.assertNotIn("max_output_tokens", value)

    def test_personal_skills_are_bounded_and_progressive(self):
        expected = {"v19-engineering", "v19-strict-proof", "v19-github-delivery"}
        paths = list((ROOT / "codex" / "skills").glob("*/SKILL.md"))
        self.assertEqual({path.parent.name for path in paths}, expected)
        for path in paths:
            text = path.read_text(encoding="utf-8")
            self.assertTrue(text.startswith("---\nname: "))
            self.assertIn("\ndescription: ", text.split("---", 2)[1])
            self.assertLessEqual(path.stat().st_size, 3000)

    def test_kernel_routes_to_personal_layers_with_fixed_budget(self):
        kernel = (ROOT / "codex" / "AGENTS.md").read_text(encoding="utf-8")
        self.assertLessEqual(len(kernel.encode("utf-8")), 10240)
        for skill in ("$v19-engineering", "$v19-strict-proof", "$v19-github-delivery"):
            self.assertIn(skill, kernel)
        for role in ("luna_execution", "sol_contract", "sol_reviewer", "terra_triage"):
            self.assertIn(role, kernel)

    def test_v21_standard_contract_extends_compatibility_owners(self):
        kernel = (ROOT / "codex" / "AGENTS.md").read_text(encoding="utf-8")
        skill = (ROOT / "codex" / "skills" / "v19-engineering" / "SKILL.md").read_text(
            encoding="utf-8"
        )
        architecture = (ROOT / "docs" / "architecture.md").read_text(encoding="utf-8")
        for document in (kernel, skill, architecture):
            for term in (
                "STANDARD",
                "acceptance",
                "likelihood",
                "recoverability",
                "complexity",
                "FOLLOW_UP",
                "replan",
            ):
                self.assertIn(term, document)
        self.assertIn("$v19-*", kernel)
        self.assertIn("stable v19-engineering", skill)
        self.assertIn("one initial review plus at most one delta review", architecture)

    def test_product_version_is_v21(self):
        manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["version"], "21.1.0")

    def test_active_identity_and_communication_contract_are_v21(self):
        root_policy = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
        architecture = (ROOT / "docs" / "architecture.md").read_text(encoding="utf-8")
        deployment = (ROOT / "docs" / "deployment.md").read_text(encoding="utf-8")
        session_context = (ROOT / "codex" / "hooks" / "session_context.py").read_text(
            encoding="utf-8"
        )
        strict_skill = (ROOT / "codex" / "skills" / "v19-strict-proof" / "SKILL.md").read_text(
            encoding="utf-8"
        )
        github_skill = (ROOT / "codex" / "skills" / "v19-github-delivery" / "SKILL.md").read_text(
            encoding="utf-8"
        )

        self.assertIn("Public release is Codex Governance Infra V21 (21.1.0)", root_policy)
        self.assertNotIn("Public release is Codex Governance Infra V19", root_policy)
        self.assertIn("V21.1.0 personal overlay", deployment)
        self.assertIn("V21 PERSONAL KERNEL", session_context)
        self.assertIn("V21 INTAKE", session_context)
        self.assertNotIn("V19 PERSONAL KERNEL", session_context)
        self.assertNotIn("V19 INTAKE", session_context)
        self.assertIn("# V21 Strict Proof (stable ID: v19-strict-proof)", strict_skill)
        self.assertIn("# V21 GitHub Delivery (stable ID: v19-github-delivery)", github_skill)

        strict_row = next(
            line for line in architecture.splitlines() if line.startswith("| `STRICT` |")
        )
        self.assertNotIn("hooks/installers", strict_row)
        self.assertIn("explicit strict selection", strict_row)
        communication = architecture.split("## Communication contract", 1)[1]
        template = communication.split("```text\n", 1)[1].split("```", 1)[0].splitlines()
        self.assertEqual(
            template,
            ["Conclusion", "Status and evidence", "Risk or next action"],
        )

    def test_strict_profile_does_not_replace_model_or_provider(self):
        path = ROOT / "codex" / "governance-strict.config.toml"
        value = path.read_text(encoding="utf-8")
        self.assertIsNone(re.search(r'^\s*model\s*=', value, re.MULTILINE))
        self.assertIsNone(re.search(r'^\s*model_provider\s*=', value, re.MULTILINE))
        self.assertEqual(quoted_scalar(value, "approval_policy"), "on-request")
        self.assertEqual(quoted_scalar(value, "sandbox_mode"), "workspace-write")
        self.assertRegex(value, r'(?m)^hooks\s*=\s*true$')
        self.assertRegex(value, r'(?m)^multi_agent\s*=\s*true$')


if __name__ == "__main__":
    unittest.main()
