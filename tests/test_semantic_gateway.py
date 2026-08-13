import pathlib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest

from codex.semantic_gateway.gateway import BackendClient, Gateway, GatewayConfig, OPERATIONS, close, doctor, load_config, query, sync


class SemanticGatewayTest(unittest.TestCase):
    def test_sync_auto_refreshes_relevant_untracked_files_with_64_file_bound(self):
        holder, root = self.repo(); self.addCleanup(holder.cleanup)
        gateway = self.configured_gateway(root)
        first = gateway.sync()
        for index in range(70):
            (root / f"new_{index}.cpp").write_text("int x() { return 1; }\n", encoding="utf-8")
        second = gateway.sync()
        paths = [item["path"] for item in second["scope_manifest"]["workset"]]
        self.assertNotEqual(first["snapshot_id"], second["snapshot_id"])
        self.assertLessEqual(len(paths), 64)
        self.assertIn("new_0.cpp", paths)

    def test_full_workset_prioritizes_changed_untracked_file_over_stable_fill(self):
        holder, root = self.repo(); self.addCleanup(holder.cleanup)
        for index in range(64):
            (root / f"seed_{index:02d}.cpp").write_text("int seed() { return 1; }\n", encoding="utf-8")
        subprocess.check_call(["git", "add", "."], cwd=root)
        subprocess.check_call(["git", "-c", "user.email=test@example.invalid", "-c", "user.name=test", "commit", "-qm", "seeds"], cwd=root)
        seeds = sorted(path.name for path in root.glob("*.cpp"))[:64]
        gateway = self.configured_gateway(root)
        gateway.config = GatewayConfig(**{**gateway.config.__dict__, "workset": tuple(seeds)})
        (root / "priority.cpp").write_text("int priority() { return 1; }\n", encoding="utf-8")
        result = gateway.sync()
        paths = [item["path"] for item in result["scope_manifest"]["workset"]]
        self.assertEqual(len(paths), 64)
        self.assertIn("priority.cpp", paths)
        self.assertNotIn(seeds[-1], paths)

    def test_query_refreshes_current_generation_but_explicit_snapshot_stays_stale(self):
        holder, root = self.repo(); self.addCleanup(holder.cleanup)
        gateway = self.configured_gateway(root)
        snapshot = gateway.sync()["snapshot_id"]
        (root / "new.cpp").write_text("int new_symbol() { return 1; }\n", encoding="utf-8")
        stale = gateway.query(snapshot, "definition", "answer")
        self.assertEqual(stale["status"], "STALE")
        fresh = gateway.sync()
        self.assertNotEqual(snapshot, fresh["snapshot_id"])

    def test_configured_build_refresh_binds_compile_database_or_reports_failure(self):
        holder, root = self.repo(); self.addCleanup(holder.cleanup)
        build = root / "build"; build.mkdir()
        command = (sys.executable, "-c", "import json,pathlib; pathlib.Path('build/compile_commands.json').write_text(json.dumps([{'file':'sample.cpp'}]))")
        gateway = Gateway(GatewayConfig(repo=root, build_dir=build, workset=("sample.cpp",),
                                        backend_command=(sys.executable, str(self.fake_backend(root))),
                                        provider_commands={"cpp": "/bin/true", "python": "/bin/true"},
                                        build_refresh_command=command))
        result = gateway.sync()
        self.assertEqual(result["build_inputs"]["refresh"]["status"], "READY")
        self.assertTrue((build / "compile_commands.json").is_file())

    def test_compile_database_uses_canonical_paths_not_same_basename(self):
        holder, root = self.repo(); self.addCleanup(holder.cleanup)
        build = root / "build"; build.mkdir()
        (root / "src").mkdir(); (root / "other").mkdir()
        (root / "src/sample.cpp").write_text("int answer() { return 42; }\n", encoding="utf-8")
        (root / "other/sample.cpp").write_text("int other() { return 1; }\n", encoding="utf-8")
        compile_db = build / "compile_commands.json"
        compile_db.write_text(json.dumps([{"file": "other/sample.cpp", "directory": str(root)}]), encoding="utf-8")
        gateway = Gateway(GatewayConfig(repo=root, build_dir=build, workset=("src/sample.cpp",),
                                        backend_command=(sys.executable, str(self.fake_backend(root))),
                                        provider_commands={"cpp": "/bin/true", "python": "/bin/true"}))
        result = gateway.sync()
        self.assertEqual(result["status"], "PARTIAL")
        self.assertEqual(result["reason"], "COMPILE_COMMANDS_REFRESH_NOT_CONFIGURED")

    def test_compile_database_accepts_relative_and_absolute_canonical_entries(self):
        holder, root = self.repo(); self.addCleanup(holder.cleanup)
        build = root / "build"; build.mkdir()
        compile_db = build / "compile_commands.json"
        compile_db.write_text(json.dumps([{"file": str(root / "sample.cpp")},
                                           {"file": "sample.cpp", "directory": str(root)}]), encoding="utf-8")
        gateway = self.configured_gateway(root)
        gateway.config = GatewayConfig(**{**gateway.config.__dict__, "build_dir": build})
        result = gateway.sync()
        self.assertIn(result["status"], {"READY", "PARTIAL"})
        self.assertNotEqual(result["reason"], "COMPILE_COMMANDS_REFRESH_NOT_CONFIGURED")

    def test_scope_compile_database_rewrites_selected_paths_and_preserves_external_arguments(self):
        holder, root = self.repo(); self.addCleanup(holder.cleanup)
        build = root / "build"; build.mkdir()
        (root / "src").mkdir(); (root / "include").mkdir()
        source = root / "src/sample.cpp"
        source.write_text("#include \"header.h\"\nint answer() { return 42; }\n", encoding="utf-8")
        (root / "include/header.h").write_text("#define ANSWER 42\n", encoding="utf-8")
        external = pathlib.Path(tempfile.mkdtemp(prefix="semantic-gateway-external-"))
        self.addCleanup(shutil.rmtree, external)
        external_include = external / "include with space"; external_include.mkdir(parents=True)
        missing_external_directory = external / "missing-build"
        compile_db = build / "compile_commands.json"
        compile_db.write_text(json.dumps([
            {"directory": str(root), "file": str(source), "output": str(build / "sample.o"),
             "command": f"clang++ -I{root / 'include'} -I '{external_include}' -DPROJECT_ROOT={root / 'src'} -o '{build / 'sample.o'}' '{source}'"},
            {"directory": "..", "file": "src/sample.cpp", "output": "build/sample.o",
             "arguments": ["clang++", "-I", "include", "-I", str(external_include),
                           "-o", "build/sample.o", "src/sample.cpp"]},
            {"directory": str(missing_external_directory), "file": str(source),
             "output": str(build / "external-dir.o"), "arguments": ["clang++", str(source)]},
        ]), encoding="utf-8")
        client = BackendClient(GatewayConfig(repo=root, build_dir=build, workset=("src/sample.cpp",)))
        scope = client._prepare_scope()
        self.addCleanup(client.close)
        self.assertEqual((scope / "src/sample.cpp").read_text(encoding="utf-8"), source.read_text(encoding="utf-8"))
        scoped_entries = json.loads((scope / "compile_commands.json").read_text(encoding="utf-8"))
        self.assertEqual(len(scoped_entries), 3)
        for entry in scoped_entries[:2]:
            self.assertEqual(entry["file"], str(scope / "src/sample.cpp"))
            self.assertEqual(entry["directory"], str(scope))
            self.assertEqual(entry["output"], str(scope / "build/sample.o"))
        self.assertEqual(scoped_entries[2]["directory"], str(missing_external_directory))
        self.assertFalse(missing_external_directory.exists())
        self.assertEqual(scoped_entries[2]["file"], str(scope / "src/sample.cpp"))
        self.assertEqual(scoped_entries[0]["command"].split(" -DPROJECT_ROOT=", 1)[1].split(" ", 1)[0],
                         str(root / "src"))
        self.assertIn(str(scope / "src/sample.cpp"), scoped_entries[0]["command"])
        self.assertIn(str(root / "include"), scoped_entries[0]["command"])
        self.assertEqual(scoped_entries[1]["arguments"][2], str(root / "include"))
        self.assertEqual(scoped_entries[1]["arguments"][4], str(external_include))

    def test_fresh_gateway_refreshes_when_cmake_graph_is_newer_than_compile_database(self):
        holder, root = self.repo(); self.addCleanup(holder.cleanup)
        build = root / "build"; build.mkdir()
        cmake = root / "CMakeLists.txt"; cmake.write_text("project(sample)\n", encoding="utf-8")
        compile_db = build / "compile_commands.json"
        compile_db.write_text(json.dumps([{"file": "sample.cpp"}]), encoding="utf-8")
        command = (sys.executable, "-c", "import pathlib; pathlib.Path('build/compile_commands.json').write_text('[]')")
        first = Gateway(GatewayConfig(repo=root, build_dir=build, workset=("sample.cpp",),
                                      backend_command=(sys.executable, str(self.fake_backend(root))),
                                      provider_commands={"cpp": "/bin/true", "python": "/bin/true"},
                                      build_refresh_command=command))
        first.sync()
        cmake.write_text("project(sample)\nset(REFRESHED ON)\n", encoding="utf-8")
        second = Gateway(GatewayConfig(repo=root, build_dir=build, workset=("sample.cpp",),
                                       backend_command=(sys.executable, str(self.fake_backend(root))),
                                       provider_commands={"cpp": "/bin/true", "python": "/bin/true"},
                                       build_refresh_command=command))
        result = second.sync()
        self.assertEqual(result["build_inputs"]["refresh"]["status"], "PARTIAL")
        self.assertEqual(result["reason"], "COMPILE_COMMANDS_INCOMPLETE")

    def test_load_config_enables_cmake_refresh_by_default_and_allows_override(self):
        holder, root = self.repo(); self.addCleanup(holder.cleanup)
        (root / "CMakeLists.txt").write_text("project(sample)\n", encoding="utf-8")
        self.assertTrue(load_config(None, root).auto_refresh_build)
        config = root / "gateway.json"
        config.write_text(json.dumps({"auto_refresh_build": False}), encoding="utf-8")
        self.assertFalse(load_config(config, root).auto_refresh_build)

    def test_query_propagates_incomplete_refresh_instead_of_backend_ready(self):
        holder, root = self.repo(); self.addCleanup(holder.cleanup)
        build = root / "build"; build.mkdir()
        (root / "CMakeLists.txt").write_text("project(sample)\n", encoding="utf-8")
        command = (sys.executable, "-c", "import pathlib; pathlib.Path('build/compile_commands.json').write_text('[]')")
        gateway = Gateway(GatewayConfig(repo=root, build_dir=build, workset=("sample.cpp",),
                                        auto_refresh_build=True, build_refresh_command=command,
                                        backend_command=(sys.executable, str(self.fake_backend(root))),
                                        provider_commands={"cpp": "/bin/true", "python": "/bin/true"}))
        snapshot = gateway.sync()["snapshot_id"]
        result = gateway.query(snapshot, "definition", "answer")
        self.assertEqual(result["status"], "PARTIAL")
        self.assertEqual(result["reason"], "COMPILE_COMMANDS_INCOMPLETE")
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

    def test_mcp_call_exposes_refresh_failure_as_partial(self):
        holder, root = self.repo(); self.addCleanup(holder.cleanup)
        build = root / "build"; build.mkdir()
        (root / "CMakeLists.txt").write_text("project(sample)\n", encoding="utf-8")
        config = root / "gateway.json"
        config.write_text(json.dumps({"repo": str(root), "build_dir": "build",
                                     "auto_refresh_build": True,
                                     "build_refresh_command": [sys.executable, "-c", "import pathlib; pathlib.Path('build/compile_commands.json').write_text('[]')"],
                                     "backend_command": [sys.executable, str(self.fake_backend(root))],
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
        request({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        process.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}) + "\n")
        process.stdin.flush()
        called = request({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
            "name": "inspect_semantic_graph", "arguments": {"repo": str(root), "operation": "definition", "symbol": "answer"}}})
        self.assertEqual(called["result"]["structuredContent"]["status"], "PARTIAL")
        self.assertEqual(called["result"]["structuredContent"]["reason"], "COMPILE_COMMANDS_INCOMPLETE")


if __name__ == "__main__":
    unittest.main()
