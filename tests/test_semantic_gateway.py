import pathlib
import json
import concurrent.futures
import shutil
import subprocess
import sys
import tempfile
import unittest

from codex.semantic_gateway.gateway import (BackendClient, Gateway, GatewayConfig, OPERATIONS,
                                            _facts_support_request, close, doctor, load_config, query, sync)


class SemanticGatewayTest(unittest.TestCase):
    def test_reviewer_counterexample_nonempty_wrong_fact_is_not_ready(self):
        payload = {"facts": [{"symbol": "other", "file": "target.cpp"}],
                   "proved_families": ["definition"]}
        self.assertFalse(_facts_support_request(payload, "definition", "answer", ("target.cpp",)))
        self.assertTrue(_facts_support_request(
            {"facts": [{"symbol": "answer", "file": "target.cpp"}],
             "proved_families": ["definition"]},
            "definition", "answer", ("target.cpp",)))

    def test_sync_auto_refreshes_relevant_untracked_files_with_64_file_bound(self):
        holder, root = self.repo(); self.addCleanup(holder.cleanup)
        gateway = self.configured_gateway(root)
        first = gateway.sync()
        for index in range(70):
            (root / f"new_{index}.cpp").write_text("int x() { return 1; }\n", encoding="utf-8")
        second = gateway.sync()
        self.assertNotEqual(first["snapshot_id"], second["snapshot_id"])
        self.assertLessEqual(second["scope_manifest"]["workset_count"], 64)
        self.assertNotIn("workset", second["scope_manifest"])

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
        self.assertEqual(result["scope_manifest"]["workset_count"], 64)
        self.assertNotIn("workset", result["scope_manifest"])

    def test_query_refreshes_current_generation_but_explicit_snapshot_stays_stale(self):
        holder, root = self.repo(); self.addCleanup(holder.cleanup)
        gateway = self.configured_gateway(root)
        snapshot = gateway.sync()["snapshot_id"]
        (root / "new.cpp").write_text("int new_symbol() { return 1; }\n", encoding="utf-8")
        stale = gateway.query(snapshot, "definition", "answer")
        self.assertEqual(stale["status"], "STALE")
        fresh = gateway.sync()
        self.assertNotEqual(snapshot, fresh["snapshot_id"])

    def test_source_only_delta_never_refreshes_build_graph_but_graph_edit_does(self):
        holder, root = self.repo(); self.addCleanup(holder.cleanup)
        build = root / "build"; build.mkdir()
        cmake = root / "CMakeLists.txt"; cmake.write_text("project(sample)\n", encoding="utf-8")
        (build / "compile_commands.json").write_text(
            json.dumps([{"file": "sample.cpp", "directory": str(root)}]), encoding="utf-8")
        counter = root / "refresh-count"
        command = (sys.executable, "-c",
                   "import pathlib; p=pathlib.Path('refresh-count'); p.write_text(str(int(p.read_text())+1) if p.exists() else '1')")
        gateway = Gateway(GatewayConfig(repo=root, build_dir=build, workset=("sample.cpp",),
                                        auto_refresh_build=True, build_refresh_command=command,
                                        backend_command=(sys.executable, str(self.fake_backend(root))),
                                        provider_commands={"cpp": "/bin/true", "python": "/bin/true"}))
        baseline = gateway.sync()
        self.assertFalse(counter.exists())
        (root / "added.cpp").write_text("int added() { return 1; }\n", encoding="utf-8")
        source_only = gateway.sync()
        self.assertEqual(source_only["status"], "PARTIAL")
        self.assertEqual(source_only["reason"], "COMPILE_COMMANDS_INCOMPLETE")
        self.assertFalse(counter.exists())
        cmake.write_text("project(sample)\nset(REFRESHED ON)\n", encoding="utf-8")
        cmake.touch()
        graph_edit = gateway.sync()
        self.assertEqual(counter.read_text(encoding="utf-8"), "1")
        self.assertEqual(graph_edit["build_inputs"]["refresh"]["returncode"], 0)

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
        self.assertEqual(cpp.workset, ())
        self.assertEqual(cpp.build_dir, root / "build")
        self.assertNotIn("module.py", cpp.workset)
        self.assertEqual(python.backend_command, ("python-backend",))
        self.assertEqual(python.profile, "python_resident")
        self.assertEqual(python.workset, ())
        self.assertEqual(python.workset, ())

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
            "  a=r['params']['arguments']; p=a.get('props', a); q=p.get('request', {}); op=p.get('question','').split(' ',1)[0] or q.get('type'); sym=a.get('symbol') or q.get('query') or q.get('from'); out={'facts':[{'symbol':sym,'operation':op,'file':'sample.cpp'}], 'proved_families':[op], 'provenance':{'backend':'fake-pinned'}}\n"
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

    def live_scope_backend(self, root):
        script = root / "live_scope_backend.py"
        script.write_text(
            "import json, pathlib, re, sys, os\n"
            "def facts():\n"
            " names=[]\n"
            " for p in pathlib.Path.cwd().rglob('*.cpp'):\n"
            "  names += re.findall(r'\\b(?:int|void|float)\\s+([A-Za-z_]\\w*)\\s*\\(', p.read_text())\n"
            " return names\n"
            "for line in sys.stdin:\n"
            " r=json.loads(line); m=r.get('method'); i=r.get('id')\n"
            " if m=='initialize': out={'protocolVersion':'2025-06-18','capabilities':{},'serverInfo':{'name':'live-fixture','version':'1'}}\n"
            " elif m=='notifications/initialized': continue\n"
            " elif m=='tools/list': out={'tools':[{'name':'inspect_code_graph'}]}\n"
            " elif m=='tools/call': out={'facts':[{'symbols':facts(),'pid':os.getpid()}], 'proved_families':['definition'], 'provenance':{'backend':'live-fixture'}}\n"
            " elif m=='shutdown': out={}\n"
            " else: out={}\n"
            " if i is not None: print(json.dumps({'jsonrpc':'2.0','id':i,'result':out}),flush=True)\n",
            encoding="utf-8")
        return script

    def test_live_backend_sees_external_scope_add_edit_delete_same_pid_session(self):
        holder, root = self.repo(); self.addCleanup(holder.cleanup)
        cache = pathlib.Path.home() / ".cache" / "codex-semantic-gateway-fixtures"
        cache.mkdir(mode=0o700, parents=True, exist_ok=True)
        state = pathlib.Path(tempfile.mkdtemp(prefix="fixture-", dir=str(cache)))
        self.addCleanup(shutil.rmtree, state, True)
        scope = state / "scope"; scope.mkdir(mode=0o700)
        config = GatewayConfig(repo=root, workset=("sample.cpp",),
                               backend_command=(sys.executable, str(self.live_scope_backend(root))),
                               provider_commands={"cpp": "/bin/true", "python": "/bin/true"})
        client = BackendClient(config, scope)
        self.addCleanup(client.close)
        shutil.copy2(root / "sample.cpp", scope / "sample.cpp")
        first = client.inspect("definition", "answer", "cpp")
        pid, session = client.process.pid, client.session_id
        self.assertIn("answer", first["facts"][0]["symbols"])
        (scope / "added.cpp").write_text("int added() { return 1; }\n", encoding="utf-8")
        (scope / "sample.cpp").write_text("int renamed() { return 2; }\n", encoding="utf-8")
        (scope / "deleted.cpp").write_text("int deleted() { return 3; }\n", encoding="utf-8")
        second = client.inspect("definition", "added", "cpp")
        self.assertEqual(client.process.pid, pid)
        self.assertEqual(client.session_id, session)
        self.assertIn("added", second["facts"][0]["symbols"])
        (scope / "deleted.cpp").unlink()
        third = client.inspect("definition", "deleted", "cpp")
        self.assertNotIn("deleted", third["facts"][0]["symbols"])

    def test_concurrent_cold_clients_elect_one_broker_and_backend(self):
        holder, root = self.repo(); self.addCleanup(holder.cleanup)
        build = root / "build"; build.mkdir()
        (build / "compile_commands.json").write_text(json.dumps([{"file": "sample.cpp", "directory": str(root)}]), encoding="utf-8")
        config = root / "gateway.json"
        config.write_text(json.dumps({"repo": str(root), "backend_command":
                                      [sys.executable, str(self.fake_backend(root))],
                                      "provider_commands": {"cpp": "/bin/true", "python": "/bin/true"},
                                      "build_dir": "build", "workset": ["sample.cpp"], "idle_ttl_sec": 5.0}), encoding="utf-8")
        request = "\n".join((
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}),
            json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
            json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
                "name": "inspect_semantic_graph", "arguments": {"repo": str(root),
                "operation": "definition", "symbol": "answer"}}}), ""))

        def invoke(_index):
            completed = subprocess.run(
                [sys.executable, str(pathlib.Path(__file__).parents[1] / "codex/bin/semantic-gateway-mcp.py"),
                 "--config", str(config)], input=request, text=True, capture_output=True,
                timeout=20, check=False)
            lines = [json.loads(line) for line in completed.stdout.splitlines() if line.strip()]
            return completed.returncode, lines[-1]["result"]["structuredContent"] if lines else {}

        with concurrent.futures.ThreadPoolExecutor(max_workers=24) as pool:
            results = list(pool.map(invoke, range(24)))
        self.assertTrue(all(code == 0 for code, _ in results))
        payloads = [payload for _, payload in results]
        self.assertTrue(all(payload.get("status") in {"READY", "PARTIAL"} for payload in payloads))
        partial = [payload for payload in payloads if payload.get("status") == "PARTIAL"]
        self.assertTrue(all(payload.get("facts") == [] and payload.get("proved_families") == []
                            and payload.get("fallback") == "bounded_exact_evidence" for payload in partial))
        ready = [payload for payload in payloads if payload.get("status") == "READY"]
        if ready:
            self.assertEqual({payload["backend"]["runtime"]["pid"] for payload in ready},
                             {ready[0]["backend"]["runtime"]["pid"]})
            self.assertEqual({payload["backend"]["runtime"]["session_id"] for payload in ready},
                             {ready[0]["backend"]["runtime"]["session_id"]})

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
        self.assertEqual(stale["fallback"], "bounded_exact_evidence")

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
        build = root / "build"; build.mkdir()
        (build / "compile_commands.json").write_text(json.dumps([{"file": "sample.cpp", "directory": str(root)}]), encoding="utf-8")
        config = root / "gateway.json"
        config.write_text(json.dumps({"repo": str(root), "backend_command": [sys.executable, str(script)],
                                     "provider_commands": {"cpp": "/bin/true", "python": "/bin/true"},
                                     "build_dir": "build", "workset": ["sample.cpp"]}), encoding="utf-8")
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

    def test_explicit_targets_exclude_noise_and_do_not_fill_first_64(self):
        holder, root = self.repo(); self.addCleanup(holder.cleanup)
        (root / "target.cpp").write_text("int target() { return 1; }\n", encoding="utf-8")
        (root / ".cache").mkdir(); (root / ".cache/noise.cpp").write_text("int noise(){}\n", encoding="utf-8")
        (root / "build-extra").mkdir(); (root / "build-extra/noise.cpp").write_text("int noise2(){}\n", encoding="utf-8")
        (root / "generated").mkdir(); (root / "generated/noise.cpp").write_text("int noise3(){}\n", encoding="utf-8")
        for index in range(80):
            (root / f"earlier_{index:02d}.cpp").write_text("int earlier() { return 1; }\n", encoding="utf-8")
        gateway = Gateway(GatewayConfig(repo=root, workset=("target.cpp",), target_paths=("target.cpp",),
                                        backend_command=(sys.executable, str(self.fake_backend(root))),
                                        provider_commands={"cpp": "/bin/true", "python": "/bin/true"}))
        result = gateway.sync()
        self.assertEqual(result["scope_manifest"]["workset_count"], 1)
        self.assertNotIn("workset", result["scope_manifest"])
        self.assertNotIn("files", result["receipt"]["repo"]["content"])
        self.assertLess(len(json.dumps(result["receipt"], separators=(",", ":"))), 8192)

    def test_mcp_shim_hot_loads_current_gateway_and_blocks_version_mismatch(self):
        holder, root = self.repo(); self.addCleanup(holder.cleanup)
        shim = pathlib.Path(__file__).parents[1] / "codex/bin/semantic-gateway-mcp.py"
        source = shim.read_text(encoding="utf-8")
        self.assertNotIn("from semantic_gateway.gateway", source)
        build = root / "build"; build.mkdir()
        (build / "compile_commands.json").write_text(json.dumps([{"file": "sample.cpp", "directory": str(root)}]), encoding="utf-8")
        config = root / "gateway.json"
        config.write_text(json.dumps({"repo": str(root), "backend_command": [sys.executable, str(self.fake_backend(root))],
                                      "provider_commands": {"cpp": "/bin/true", "python": "/bin/true"},
                                      "build_dir": "build", "workset": ["sample.cpp"], "version": "21.3.0"}), encoding="utf-8")
        process = subprocess.Popen([sys.executable, str(shim), "--config", str(config)], stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, text=True)
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
        process.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
        process.stdin.flush()
        call = {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
            "name": "inspect_semantic_graph", "arguments": {"repo": str(root), "config": str(config),
            "operation": "definition", "symbol": "answer", "target_paths": ["sample.cpp"]}}}
        first = request(call)["result"]["structuredContent"]
        self.assertEqual(first["status"], "READY")
        gateway_path = pathlib.Path(__file__).parents[1] / "codex/semantic_gateway/gateway.py"
        original = gateway_path.read_text(encoding="utf-8")
        original_config = config.read_text(encoding="utf-8")
        manifest = config.with_name("semantic-tools.v21.json")
        try:
            gateway_path.write_text(original.replace('VERSION = "21.3.0"', 'VERSION = "21.3.1"', 1), encoding="utf-8")
            config.write_text(original_config.replace('21.3.0', '21.3.1'), encoding="utf-8")
            manifest.write_text('{"version":"21.3.1"}\n', encoding="utf-8")
            call["id"] = 3
            payload = request(call)["result"]["structuredContent"]
            self.assertEqual(payload["status"], "READY")
            self.assertEqual(payload["version"], "21.3.1")
            config.write_text(original_config.replace('21.3.0', '21.2.0'), encoding="utf-8")
            manifest.write_text('{"version":"21.2.0"}\n', encoding="utf-8")
            call["id"] = 4
            mismatch = request(call)["result"]["structuredContent"]
            self.assertEqual(mismatch["status"], "SEMANTIC_CAPABILITY_BLOCKED")
            self.assertEqual(mismatch["reason"], "SEMANTIC_VERSION_MISMATCH")
        finally:
            gateway_path.write_text(original, encoding="utf-8")
            config.write_text(original_config, encoding="utf-8")
            manifest.unlink(missing_ok=True)

    def test_mcp_shim_is_thin_and_within_rss_budget(self):
        shim = pathlib.Path(__file__).parents[1] / "codex/bin/semantic-gateway-mcp.py"
        source = shim.read_text(encoding="utf-8")
        self.assertNotIn("semantic_gateway.gateway", source)
        self.assertNotIn("samchon-graph", source)
        process = subprocess.Popen([sys.executable, str(shim)], stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, text=True)
        try:
            process.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}) + "\n")
            process.stdin.flush()
            response = json.loads(process.stdout.readline())
            self.assertEqual(response["result"]["serverInfo"]["version"], "21.3.0")
            rss_kib = int(next(line.split()[1] for line in pathlib.Path(f"/proc/{process.pid}/status").read_text().splitlines()
                               if line.startswith("VmRSS:")))
            self.assertLess(rss_kib, 20 * 1024)
        finally:
            process.kill(); process.wait()
            process.stdin.close(); process.stdout.close()

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
        self.assertEqual(called["result"]["structuredContent"]["status"], "SEMANTIC_CAPABILITY_BLOCKED")
        self.assertIn(called["result"]["structuredContent"]["reason"],
                      {"SEMANTIC_COLD_DEADLINE", "SEMANTIC_CAPABILITY_BLOCKED"})


if __name__ == "__main__":
    unittest.main()
