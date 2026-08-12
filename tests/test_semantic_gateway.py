import pathlib
import json
import subprocess
import sys
import tempfile
import unittest

from codex.semantic_gateway.gateway import BackendClient, Gateway, GatewayConfig, OPERATIONS, close, doctor, load_config, query, sync


class SemanticGatewayTest(unittest.TestCase):
    def test_config_routes_language_and_derives_repo_local_workset(self):
        holder, root = self.repo(); self.addCleanup(holder.cleanup)
        (root / "module.py").write_text("def answer(): return 42\n", encoding="utf-8")
        (root / "build").mkdir()
        (root / "build/compile_commands.json").write_text("[]\n", encoding="utf-8")
        subprocess.check_call(["git", "add", "module.py"], cwd=root)
        config = root / "gateway.json"
        config.write_text(json.dumps({
            "backend_command": ["legacy"],
            "backend_commands": {"cpp": ["cpp-backend"], "python": ["python-backend"]},
            "profiles": {"cpp": "cpp_resident", "python": "python_resident"},
        }), encoding="utf-8")
        cpp = load_config(config, root, "cpp")
        python = load_config(config, root, "python")
        self.assertEqual(cpp.backend_command, ("cpp-backend",))
        self.assertEqual(cpp.profile, "cpp_resident")
        self.assertIn("sample.cpp", cpp.workset)
        self.assertEqual(cpp.build_dir, root / "build")
        self.assertNotIn("module.py", cpp.workset)
        self.assertEqual(python.backend_command, ("python-backend",))
        self.assertEqual(python.profile, "python_resident")
        self.assertIn("module.py", python.workset)
        self.assertNotIn("sample.cpp", python.workset)

    def repo(self):
        directory = tempfile.TemporaryDirectory()
        root = pathlib.Path(directory.name)
        subprocess.check_call(["git", "init", "-q"], cwd=root)
        (root / "sample.cpp").write_text("int answer() { return 42; }\n", encoding="utf-8")
        subprocess.check_call(["git", "add", "sample.cpp"], cwd=root)
        subprocess.check_call(["git", "-c", "user.email=test@example.invalid", "-c", "user.name=test", "commit", "-qm", "sample"], cwd=root)
        return directory, root

    def fake_backend(self, root):
        script = root / "fake_backend.py"
        script.write_text(
            "import json,sys\n"
            "for line in sys.stdin:\n"
            " r=json.loads(line); m=r.get('method'); i=r.get('id')\n"
            " if m=='initialize': out={'protocolVersion':'2025-06-18','capabilities':{},'serverInfo':{'name':'fake','version':'1'}}\n"
            " elif m=='notifications/initialized': continue\n"
            " elif m=='tools/list': out={'tools':[{'name':'inspect_code_graph','inputSchema':{'type':'object'}}]}\n"
            " elif m=='tools/call':\n"
            "  a=r['params']['arguments']; p=a.get('props', a); q=p.get('request', {}); op=p.get('question','').split(' ',1)[0] or q.get('type'); sym=a.get('symbol') or q.get('query') or q.get('from'); out={'facts':[{'symbol':sym,'operation':op}], 'proved_families':[op], 'provenance':{'backend':'fake-pinned'}}\n"
            " elif m=='shutdown': out={}\n"
            " else: out={}\n"
            " if i is not None: print(json.dumps({'jsonrpc':'2.0','id':i,'result':out}),flush=True)\n",
            encoding="utf-8")
        return script

    def configured_gateway(self, root, config_path=None):
        script = self.fake_backend(root)
        return Gateway(GatewayConfig(repo=root, backend_command=(sys.executable, str(script)),
                                     provider_commands={"cpp": "/bin/true", "python": "/bin/true"},
                                     workset=("sample.cpp",), config_path=config_path))

    def test_doctor_is_normalized_and_truthful_when_tools_are_missing(self):
        holder, root = self.repo()
        self.addCleanup(holder.cleanup)
        result = doctor(root)
        self.assertIn(result["status"], {"READY", "PARTIAL"})
        for key in ("repo_digest", "build_inputs", "provider_versions", "scope_manifest",
                    "generation", "requested_families", "proved_families", "facts", "missing",
                    "fallback", "resources", "identity"):
            self.assertIn(key, result)
        self.assertEqual(result["requested_families"], list(OPERATIONS))
        self.assertTrue(result["truthful"])

    def test_sync_query_and_stale_snapshot_contract(self):
        holder, root = self.repo()
        self.addCleanup(holder.cleanup)
        result = sync(root, {"workset": ["sample.cpp"]}, "cpp_resident")
        self.assertEqual(result["operation"], "sync")
        self.assertTrue(result["snapshot_id"].startswith("sgw-"))
        unavailable = query(result["snapshot_id"], "definition", "answer")
        self.assertIn(unavailable["status"], {"NOT_READY", "PARTIAL"})
        self.assertIn("fallback", unavailable)
        (root / "sample.cpp").write_text("int answer() { return 43; }\n", encoding="utf-8")
        stale = query(result["snapshot_id"], "impact", "answer")
        self.assertEqual(stale["status"], "STALE")
        self.assertEqual(stale["fallback"], "exact_evidence")

    def test_operation_set_and_close(self):
        holder, root = self.repo()
        self.addCleanup(holder.cleanup)
        self.assertEqual(close(root)["operation"], "close")
        with self.assertRaises(ValueError):
            query("missing", "not_an_operation", "x")

    def test_real_backend_lifecycle_maps_inspect_code_graph_facts(self):
        holder, root = self.repo(); self.addCleanup(holder.cleanup)
        gateway = self.configured_gateway(root)
        ready = gateway.doctor()
        self.assertEqual(ready["status"], "READY")
        self.assertEqual(ready["result"]["facts"][0]["symbol"], "__codex_semantic_gateway_probe__")
        snapshot = gateway.sync()["snapshot_id"]
        result = gateway.query(snapshot, "definition", "answer")
        self.assertEqual(result["status"], "READY")
        self.assertEqual(result["result"]["facts"][0]["operation"], "definition")
        self.assertEqual(result["provenance"]["backend"], "fake-pinned")

    def test_snapshot_cannot_cross_language_backend(self):
        holder, root = self.repo(); self.addCleanup(holder.cleanup)
        gateway = self.configured_gateway(root)
        snapshot = gateway.sync()["snapshot_id"]
        result = gateway.query(snapshot, "definition", "answer", "python")
        self.assertEqual(result["status"], "STALE")
        self.assertEqual(result["reason"], "SNAPSHOT_LANGUAGE_MISMATCH")
        self.assertEqual(result["query"]["language"], "python")

    def test_snapshot_cannot_cross_repository(self):
        holder_a, root_a = self.repo(); self.addCleanup(holder_a.cleanup)
        holder_b, root_b = self.repo(); self.addCleanup(holder_b.cleanup)
        snapshot = self.configured_gateway(root_a).sync()["snapshot_id"]
        result = self.configured_gateway(root_b).query(snapshot, "definition", "answer", "cpp")
        self.assertEqual(result["status"], "STALE")
        self.assertEqual(result["reason"], "SNAPSHOT_REPOSITORY_MISMATCH")
        self.assertEqual(result["requested_repo"], str(root_b))

    def test_backend_accepts_standard_mcp_structured_content(self):
        holder, root = self.repo(); self.addCleanup(holder.cleanup)
        client = BackendClient(GatewayConfig(repo=root, backend_command=("unused",),
                                             workset=("sample.cpp",)))
        client.start = lambda: {"status": "READY"}
        client._request = lambda *_args, **_kwargs: {
            "content": [],
            "structuredContent": {"audit": "checked", "next": {"action": "answer"},
                                  "result": {"type": "lookup", "hits": [{"name": "answer"}]}}}
        result = client.inspect("definition", "answer", "cpp")
        self.assertEqual(result["result"]["hits"][0]["name"], "answer")
        self.assertEqual(result["audit"], "checked")

    def test_content_config_build_and_workset_identity_make_snapshot_stale(self):
        holder, root = self.repo(); self.addCleanup(holder.cleanup)
        config = root / "gateway.json"; config.write_text("{}\n", encoding="utf-8")
        gateway = self.configured_gateway(root, config)
        snapshot = gateway.sync()["snapshot_id"]
        (root / "sample.cpp").write_text("int answer() { return 43; }\n", encoding="utf-8")
        self.assertEqual(gateway.query(snapshot, "impact", "answer")["status"], "STALE")
        config.write_text("{\"changed\":true}\n", encoding="utf-8")
        fresh = self.configured_gateway(root, config).sync()["snapshot_id"]
        self.assertNotEqual(snapshot, fresh)

    def test_staged_and_untracked_content_identity_is_snapshot_bound(self):
        holder, root = self.repo(); self.addCleanup(holder.cleanup)
        gateway = self.configured_gateway(root)
        snapshot = gateway.sync()["snapshot_id"]
        (root / "staged.cpp").write_text("int staged() { return 1; }\n", encoding="utf-8")
        subprocess.check_call(["git", "add", "staged.cpp"], cwd=root)
        self.assertEqual(gateway.query(snapshot, "definition", "answer")["status"], "STALE")
        fresh = gateway.sync()["snapshot_id"]
        (root / "untracked.cpp").write_text("int untracked() { return 2; }\n", encoding="utf-8")
        self.assertEqual(gateway.query(fresh, "definition", "answer")["status"], "STALE")

    def test_mcp_stdio_handshake_tools_list_and_call(self):
        holder, root = self.repo(); self.addCleanup(holder.cleanup)
        script = self.fake_backend(root)
        config = root / "gateway.json"
        config.write_text(json.dumps({"repo": str(root), "backend_command": [sys.executable, str(script)],
                                     "provider_commands": {"cpp": "/bin/true", "python": "/bin/true"},
                                     "workset": ["sample.cpp"]}), encoding="utf-8")
        process = subprocess.Popen([sys.executable, str(pathlib.Path(__file__).parents[1] / "codex/bin/semantic-gateway-mcp.py"), "--config", str(config)],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        def stop_process():
            if process.poll() is None:
                process.kill()
            process.wait()
            if process.stdin: process.stdin.close()
            if process.stdout: process.stdout.close()
        self.addCleanup(stop_process)
        def request(value):
            process.stdin.write(json.dumps(value) + "\n"); process.stdin.flush()
            return json.loads(process.stdout.readline())
        self.assertEqual(request({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})["result"]["serverInfo"]["name"], "codex-semantic-gateway")
        process.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}) + "\n"); process.stdin.flush()
        listed = request({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        self.assertEqual(listed["result"]["tools"][0]["name"], "inspect_semantic_graph")
        called = request({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "inspect_semantic_graph", "arguments": {"repo": str(root), "operation": "definition", "symbol": "answer"}}})
        payload = called["result"]["structuredContent"]
        self.assertEqual(payload["status"], "READY")
        self.assertEqual(payload["result"]["facts"][0]["operation"], "definition")


if __name__ == "__main__":
    unittest.main()
