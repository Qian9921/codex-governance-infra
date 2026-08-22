from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts.github_delivery import DeliveryFlow, FlowError, GHClient, ReviewVerdict


class FakeRunner:
    def __init__(self, responses: list[tuple[int, str, str]]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[tuple[str, ...], str]] = []

    def __call__(
        self, command: tuple[str, ...], env: dict[str, str]
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append((tuple(command), env["GH_CONFIG_DIR"]))
        if not self.responses:
            raise AssertionError(f"unexpected command: {command}")
        code, stdout, stderr = self.responses.pop(0)
        return subprocess.CompletedProcess(command, code, stdout, stderr)


def user(login: str) -> tuple[int, str, str]:
    return 0, json.dumps({"login": login}), ""


class GithubDeliveryTests(unittest.TestCase):
    def client_pair(self, runner: FakeRunner) -> DeliveryFlow:
        return DeliveryFlow(
            GHClient(Path("/profiles/author"), runner),
            GHClient(Path("/profiles/reviewer"), runner),
            "author-one",
            "reviewer-two",
        )

    def test_preflight_uses_distinct_config_directories_and_accounts(self) -> None:
        runner = FakeRunner([user("author-one"), user("reviewer-two")])
        flow = self.client_pair(runner)

        self.assertEqual(flow.preflight(), ("author-one", "reviewer-two"))
        self.assertEqual(
            [config for _, config in runner.calls],
            ["/profiles/author", "/profiles/reviewer"],
        )

    def test_preflight_rejects_same_account(self) -> None:
        runner = FakeRunner([user("same"), user("SAME")])
        flow = self.client_pair(runner)

        with self.assertRaisesRegex(FlowError, "different"):
            flow.preflight()

    def test_submit_review_rejects_stale_head(self) -> None:
        runner = FakeRunner([(0, '{"head":{"sha":"new-head"}}', "")])
        client = GHClient(Path("/profiles/reviewer"), runner)

        with self.assertRaisesRegex(FlowError, "stale"):
            client.submit_review(
                "owner/repo", 23, ReviewVerdict("old-head", "APPROVE", "Reviewed.")
            )

    def test_find_or_create_pr_resolves_created_url_to_number(self) -> None:
        runner = FakeRunner(
            [
                (0, "[]", ""),
                (0, "https://github.com/owner/repo/pull/23\n", ""),
                (0, '{"number":23}', ""),
            ]
        )
        client = GHClient(Path("/profiles/author"), runner)

        self.assertEqual(
            client.find_or_create_pr("owner/repo", "feature/v23", "main", "V23", "body"),
            23,
        )
        self.assertIn(
            (
                "gh",
                "pr",
                "view",
                "https://github.com/owner/repo/pull/23",
                "--repo",
                "owner/repo",
                "--json",
                "number",
            ),
            [call for call, _ in runner.calls],
        )

    def test_latest_reviewer_approval_is_required_for_current_head(self) -> None:
        reviews = json.dumps(
            [
                {"state": "APPROVED", "commit_id": "old-head", "user": {"login": "reviewer-two"}},
                {"state": "COMMENTED", "commit_id": "new-head", "user": {"login": "reviewer-two"}},
            ]
        )
        runner = FakeRunner([(0, reviews, "")])
        client = GHClient(Path("/profiles/author"), runner)

        self.assertFalse(client.has_current_approval("owner/repo", 23, "reviewer-two", "new-head"))

    def test_latest_reviewer_request_changes_invalidates_approval(self) -> None:
        reviews = json.dumps(
            [
                {"state": "APPROVED", "commit_id": "head-23", "user": {"login": "reviewer-two"}},
                {
                    "state": "CHANGES_REQUESTED",
                    "commit_id": "head-23",
                    "user": {"login": "reviewer-two"},
                },
            ]
        )
        runner = FakeRunner([(0, reviews, "")])
        client = GHClient(Path("/profiles/author"), runner)

        self.assertFalse(client.has_current_approval("owner/repo", 23, "reviewer-two", "head-23"))

    def test_comment_after_current_approval_does_not_revoke_it(self) -> None:
        reviews = json.dumps(
            [
                {"state": "APPROVED", "commit_id": "head-23", "user": {"login": "reviewer-two"}},
                {"state": "COMMENTED", "commit_id": "head-23", "user": {"login": "reviewer-two"}},
            ]
        )
        runner = FakeRunner([(0, reviews, "")])
        client = GHClient(Path("/profiles/author"), runner)

        self.assertTrue(client.has_current_approval("owner/repo", 23, "reviewer-two", "head-23"))

    def test_merge_uses_expected_head_and_never_merges_stale_head(self) -> None:
        runner = FakeRunner(
            [
                user("author-one"),
                user("reviewer-two"),
                (0, '{"head":{"sha":"head-23"}}', ""),
                (0, '{"data":{"repository":{"pullRequest":{"reviewThreads":{"nodes":[]}}}}}', ""),
                (0, "", ""),
                (
                    0,
                    '[{"state":"APPROVED","commit_id":"head-23","user":{"login":"reviewer-two"}}]',
                    "",
                ),
                (0, '{"head":{"sha":"head-23"}}', ""),
                (0, "", ""),
            ]
        )
        flow = self.client_pair(runner)

        flow.merge_if_ready("owner/repo", 23, "head-23")
        merge_calls = [call for call, _ in runner.calls if call[:3] == ("gh", "pr", "merge")]
        self.assertEqual(len(merge_calls), 1)
        self.assertIn("--repo", merge_calls[0])
        self.assertIn("owner/repo", merge_calls[0])
        self.assertIn("--match-head-commit", merge_calls[0])
        self.assertIn("head-23", merge_calls[0])

    def test_merge_rejects_head_change_before_merge(self) -> None:
        runner = FakeRunner(
            [
                user("author-one"),
                user("reviewer-two"),
                (0, '{"head":{"sha":"new-head"}}', ""),
            ]
        )
        flow = self.client_pair(runner)

        with self.assertRaisesRegex(FlowError, "changed"):
            flow.merge_if_ready("owner/repo", 23, "old-head")

    def test_preflight_rejects_identity_that_disagrees_with_local_mapping(self) -> None:
        runner = FakeRunner([user("wrong-author"), user("reviewer-two")])
        flow = self.client_pair(runner)

        with self.assertRaisesRegex(FlowError, "does not match"):
            flow.preflight()

    def test_push_uses_author_credential_helper_not_ambient_identity(self) -> None:
        runner = FakeRunner(
            [
                user("author-one"),
                user("reviewer-two"),
                (0, "https://github.com/owner/repo.git\n", ""),
                (0, "", ""),
            ]
        )
        flow = DeliveryFlow(
            GHClient(Path("/profiles/author"), runner),
            GHClient(Path("/profiles/reviewer"), runner),
            "author-one",
            "reviewer-two",
            git_runner=runner,
        )
        with tempfile.TemporaryDirectory() as directory:
            flow.push_branch(Path(directory), "origin", "HEAD:refs/heads/feature/v23")

        command, config_dir = runner.calls[-1]
        self.assertEqual(config_dir, "/profiles/author")
        self.assertEqual(command[:3], ("git", "-C", str(Path(command[2]))))
        self.assertIn("credential.helper=", command)
        self.assertIn("credential.helper=!gh auth git-credential", command)
        self.assertIn("http.extraHeader=", command)
        self.assertIn("http.https://github.com/.extraHeader=", command)
        self.assertEqual(
            command[-3:],
            ("push", "https://github.com/owner/repo.git", "HEAD:refs/heads/feature/v23"),
        )

    def test_push_rejects_non_https_or_credentialed_remote(self) -> None:
        for remote_url in (
            "git@github.com:owner/repo.git\n",
            "https://token@github.com/owner/repo.git\n",
        ):
            runner = FakeRunner([user("author-one"), user("reviewer-two"), (0, remote_url, "")])
            flow = DeliveryFlow(
                GHClient(Path("/profiles/author"), runner),
                GHClient(Path("/profiles/reviewer"), runner),
                "author-one",
                "reviewer-two",
                git_runner=runner,
            )
            with (
                tempfile.TemporaryDirectory() as directory,
                self.assertRaisesRegex(FlowError, "credential-free HTTPS"),
            ):
                flow.push_branch(Path(directory), "origin", "HEAD:refs/heads/feature/v23")
            self.assertFalse(any("push" in call for call, _ in runner.calls))


if __name__ == "__main__":
    unittest.main()
