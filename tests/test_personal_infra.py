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
