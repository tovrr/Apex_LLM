import importlib
import os
import tempfile
import unittest

from fastapi.testclient import TestClient

# Isolated temp DB so tests never touch the production key store.
_tmp_db = os.path.join(tempfile.mkdtemp(), "apex_keys_test.db")


class TestApiSmoke(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        # Keep tests deterministic and avoid heavy model download/load.
        os.environ["APEX_API_KEY"] = "test-key"
        os.environ["APEX_SKIP_MODEL_LOAD"] = "1"
        os.environ["APEX_RATE_LIMIT_PER_WINDOW"] = "100"
        os.environ["APEX_OPENAI_COMPAT_URL"] = ""
        # Point key_store at an isolated temp file for this test run.
        os.environ["APEX_KEYS_DB"] = _tmp_db

        import key_store
        import serveur_api

        # Reload both modules so the env vars above take effect.
        importlib.reload(key_store)
        cls.serveur_api = importlib.reload(serveur_api)
        os.environ["APEX_SKIP_MODEL_LOAD"] = "1"
        os.environ["APEX_OPENAI_COMPAT_URL"] = ""
        os.environ["APEX_OLLAMA_URL"] = ""
        setattr(cls.serveur_api, "APEX_OPENAI_COMPAT_URL", "")
        setattr(cls.serveur_api, "APEX_OLLAMA_URL", "")
        try:
            cls.serveur_api.key_store.add_key("test-key", label="test-key", plan="internal")
        except ValueError:
            pass
        cls.client = TestClient(cls.serveur_api.app)

    def test_health_ok(self) -> None:
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json().get("status"), "ok")

    def test_request_id_header_is_set(self) -> None:
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertIn("X-Request-ID", response.headers)

    def test_request_id_header_is_forwarded(self) -> None:
        response = self.client.get("/health", headers={"X-Request-ID": "req-test-123"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get("X-Request-ID"), "req-test-123")

    def test_status_endpoint_ok(self) -> None:
        response = self.client.get("/api/status")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body.get("service"), "apex-llm")
        self.assertIn("model", body)

    def test_chat_rejects_invalid_api_key(self) -> None:
        response = self.client.post(
            "/chat",
            headers={"X-API-Key": "wrong-key"},
            json={"question": "Bonjour", "mots_max": 20},
        )
        self.assertEqual(response.status_code, 403)

    def test_chat_validates_mots_max_upper_bound(self) -> None:
        response = self.client.post(
            "/chat",
            headers={"X-API-Key": "test-key"},
            json={"question": "Bonjour", "mots_max": 501},
        )
        self.assertEqual(response.status_code, 422)

    def test_chat_success_with_valid_payload(self) -> None:
        response = self.client.post(
            "/chat",
            headers={"X-API-Key": "test-key"},
            json={"question": "Bonjour", "mots_max": 20},
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body.get("status"), "succes")
        self.assertIn("reponse_apex", body)
        self.assertIn("request_id", body)
        self.assertIn("model_tier", body)

    def test_chat_accepts_explicit_model_tier(self) -> None:
        response = self.client.post(
            "/chat",
            headers={"X-API-Key": "test-key"},
            json={"question": "Bonjour", "mots_max": 20, "model_tier": "fast"},
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body.get("model_tier"), "fast")

    def test_chat_rejects_invalid_model_tier(self) -> None:
        response = self.client.post(
            "/chat",
            headers={"X-API-Key": "test-key"},
            json={"question": "Bonjour", "mots_max": 20, "model_tier": "ultra"},
        )
        self.assertEqual(response.status_code, 422)

    def test_chat_stream_success_with_valid_payload(self) -> None:
        with self.client.stream(
            "POST",
            "/chat/stream",
            headers={"X-API-Key": "test-key"},
            json={"question": "Bonjour", "mots_max": 20},
        ) as response:
            self.assertEqual(response.status_code, 200)
            content = "".join(response.iter_text())

        self.assertIn('"type": "status"', content)
        self.assertIn('"type": "done"', content)
        self.assertIn('"model_tier": "default"', content)

    def test_chat_v2_success_with_context_and_tools(self) -> None:
        payload = {
            "messages": [
                {"role": "system", "content": "You are helpful."},
                {"role": "user", "content": "Use weather_tool for Paris."},
            ],
            "mots_max": 30,
            "task_type": "reasoning",
            "model_tier": "reasoning",
            "context_chunks": [
                {"source": "docs/weather.md", "content": "Paris weather is mild.", "score": 0.91}
            ],
            "tools": [
                {"name": "weather_tool", "description": "Get weather", "input_schema": {"city": "string"}}
            ],
            "tool_choice": "auto",
        }
        response = self.client.post("/chat/v2", headers={"X-API-Key": "test-key"}, json=payload)
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body.get("status"), "succes")
        self.assertIn("reponse_apex", body)
        self.assertIn("tool_calls", body)
        self.assertIn("citations", body)
        self.assertIn("docs/weather.md", body.get("citations", []))
        self.assertEqual(body.get("model_tier"), "reasoning")

    def test_chat_v2_rejects_invalid_api_key(self) -> None:
        payload = {
            "messages": [{"role": "user", "content": "Hello"}],
            "mots_max": 10,
        }
        response = self.client.post("/chat/v2", headers={"X-API-Key": "wrong-key"}, json=payload)
        self.assertEqual(response.status_code, 403)

    def test_openai_chat_ignores_forced_tool_not_declared(self) -> None:
        payload = {
            "model": "apex:fast",
            "messages": [{"role": "user", "content": "Hello there"}],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "weather_tool",
                        "description": "Get weather",
                        "parameters": {"type": "object", "properties": {}},
                    },
                }
            ],
            "tool_choice": {"type": "function", "function": {"name": "different_tool"}},
            "stream": False,
            "max_tokens": 20,
        }

        response = self.client.post("/v1/chat/completions", json=payload)
        self.assertEqual(response.status_code, 200)
        body = response.json()
        choice = body["choices"][0]
        self.assertEqual(choice["finish_reason"], "stop")
        self.assertEqual(choice["message"]["role"], "assistant")
        self.assertIsNotNone(choice["message"]["content"])
        self.assertNotIn("tool_calls", choice["message"])


class TestKeyStore(unittest.TestCase):
    """Unit tests for the key_store module using an isolated in-memory-equivalent DB."""

    def setUp(self) -> None:
        import tempfile
        self._tmp_dir = tempfile.mkdtemp()
        self._db_path = os.path.join(self._tmp_dir, "ks_unit_test.db")
        os.environ["APEX_KEYS_DB"] = self._db_path

        import key_store
        importlib.reload(key_store)
        self.ks = key_store
        self.ks.init_db()

    def test_add_and_verify_key(self) -> None:
        raw = "test-raw-key-abc"
        self.ks.add_key(raw, label="unit-test", plan="free")
        info = self.ks.verify_key(raw)
        self.assertIsNotNone(info)
        assert info is not None
        self.assertEqual(info.label, "unit-test")
        self.assertEqual(info.plan, "free")

    def test_verify_unknown_key_returns_none(self) -> None:
        result = self.ks.verify_key("does-not-exist")
        self.assertIsNone(result)

    def test_duplicate_key_raises(self) -> None:
        self.ks.add_key("dup-key", label="a", plan="free")
        with self.assertRaises(ValueError):
            self.ks.add_key("dup-key", label="b", plan="free")

    def test_revoke_key(self) -> None:
        self.ks.add_key("revoke-me", label="r", plan="free")
        info_before = self.ks.verify_key("revoke-me")
        self.assertIsNotNone(info_before)
        assert info_before is not None
        revoked = self.ks.revoke_key(info_before.key_hash)
        self.assertTrue(revoked)
        self.assertIsNone(self.ks.verify_key("revoke-me"))

    def test_quota_check_passes_under_limit(self) -> None:
        self.ks.add_key("quota-ok", label="q", plan="pro")
        info = self.ks.verify_key("quota-ok")
        assert info is not None
        # Should not raise
        self.ks.check_quota(info.key_hash)

    def test_quota_exceeded_after_recording(self) -> None:
        # Add a key with a limit of 1 req/day
        self.ks.add_key("tight-key", label="t", plan="free", quota_req_per_day=1)
        info = self.ks.verify_key("tight-key")
        assert info is not None
        self.ks.record_usage(info.key_hash, tokens_used=10)
        with self.assertRaises(self.ks.QuotaExceededError):
            self.ks.check_quota(info.key_hash)

    def test_list_keys_returns_entries(self) -> None:
        self.ks.add_key("list-key", label="list-test", plan="internal")
        keys = self.ks.list_keys()
        labels = [k["label"] for k in keys]
        self.assertIn("list-test", labels)

    def test_invalid_plan_raises(self) -> None:
        with self.assertRaises(ValueError):
            self.ks.add_key("bad-plan-key", label="x", plan="enterprise")

    # ── S2: usage_events ledger ───────────────────────────────────────────────

    def test_record_usage_creates_event_row(self) -> None:
        self.ks.add_key("event-key", label="ev", plan="pro")
        info = self.ks.verify_key("event-key")
        assert info is not None
        event_id = self.ks.record_usage(
            info.key_hash, tokens_used=42, endpoint="/chat", latency_ms=120
        )
        self.assertIsInstance(event_id, str)
        self.assertTrue(len(event_id) == 32)  # UUID hex

    def test_get_usage_summary_structure(self) -> None:
        self.ks.add_key("summary-key", label="summ", plan="pro")
        info = self.ks.verify_key("summary-key")
        assert info is not None
        self.ks.record_usage(info.key_hash, tokens_used=100, endpoint="/chat", latency_ms=50)
        summary = self.ks.get_usage_summary(info.key_hash, days=7)
        self.assertIn("totals", summary)
        self.assertIn("daily", summary)
        self.assertIn("recent_events", summary)
        self.assertEqual(summary["totals"]["tokens"], 100)
        self.assertEqual(summary["totals"]["requests"], 1)
        self.assertEqual(len(summary["recent_events"]), 1)
        self.assertEqual(summary["recent_events"][0]["latency_ms"], 50)

    def test_get_usage_summary_unknown_key(self) -> None:
        result = self.ks.get_usage_summary("nonexistent-hash", days=7)
        self.assertEqual(result, {})


class TestUsageEndpoint(unittest.TestCase):
    """Integration tests for GET /api/usage."""

    @classmethod
    def setUpClass(cls) -> None:
        import tempfile
        _db = os.path.join(tempfile.mkdtemp(), "apex_keys_usage_test.db")
        os.environ["APEX_API_KEY"] = "test-key"
        os.environ["APEX_SKIP_MODEL_LOAD"] = "1"
        os.environ["APEX_RATE_LIMIT_PER_WINDOW"] = "100"
        os.environ["APEX_OPENAI_COMPAT_URL"] = ""
        os.environ["APEX_KEYS_DB"] = _db

        import key_store
        import serveur_api
        importlib.reload(key_store)
        cls.serveur_api = importlib.reload(serveur_api)
        os.environ["APEX_SKIP_MODEL_LOAD"] = "1"
        os.environ["APEX_OPENAI_COMPAT_URL"] = ""
        os.environ["APEX_OLLAMA_URL"] = ""
        setattr(cls.serveur_api, "APEX_OPENAI_COMPAT_URL", "")
        setattr(cls.serveur_api, "APEX_OLLAMA_URL", "")
        try:
            cls.serveur_api.key_store.add_key("test-key", label="test-key", plan="internal")
        except ValueError:
            pass
        cls.client = TestClient(cls.serveur_api.app)

    def test_usage_endpoint_rejects_invalid_key(self) -> None:
        response = self.client.get("/api/usage", headers={"X-API-Key": "wrong"})
        self.assertEqual(response.status_code, 403)

    def test_usage_endpoint_returns_summary(self) -> None:
        response = self.client.get("/api/usage", headers={"X-API-Key": "test-key"})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertIn("totals", body)
        self.assertIn("daily", body)
        self.assertIn("recent_events", body)
        self.assertIn("plan", body)

    def test_tools_contract_endpoint(self) -> None:
        response = self.client.get("/api/tools")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body.get("tool_calling", {}).get("supported"))
        self.assertTrue(body.get("retrieval", {}).get("supported"))
        self.assertTrue(body.get("model_routing", {}).get("supported"))
        self.assertIn("default", body.get("model_routing", {}).get("tiers", []))


class TestPublicPages(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        os.environ["APEX_API_KEY"] = "test-key"
        os.environ["APEX_SKIP_MODEL_LOAD"] = "1"
        os.environ["APEX_RATE_LIMIT_PER_WINDOW"] = "100"
        os.environ["APEX_OPENAI_COMPAT_URL"] = ""
        os.environ["APEX_OLLAMA_URL"] = ""

        import serveur_api

        cls.serveur_api = importlib.reload(serveur_api)
        setattr(cls.serveur_api, "APEX_OPENAI_COMPAT_URL", "")
        setattr(cls.serveur_api, "APEX_OLLAMA_URL", "")
        cls.client = TestClient(cls.serveur_api.app)

    def test_developer_page_available(self) -> None:
        response = self.client.get("/developer")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Apex Developer API", response.text)

    def test_pricing_page_available(self) -> None:
        response = self.client.get("/pricing")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Apex Pricing Draft", response.text)


class TestEvalScorer(unittest.TestCase):
    """Unit tests for the eval runner's scoring function."""

    def setUp(self) -> None:
        import sys
        import os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        from evals.run_evals import score_response
        self.score = score_response

    def test_all_must_contain_present(self) -> None:
        result = self.score(
            "The capital of France is Paris.",
            {"must_contain": ["Paris"], "must_not_contain": []},
        )
        self.assertTrue(result["passed"])
        self.assertEqual(result["score"], 1.0)

    def test_missing_required_phrase(self) -> None:
        result = self.score(
            "The capital of France is Lyon.",
            {"must_contain": ["Paris"], "must_not_contain": []},
        )
        self.assertFalse(result["passed"])
        self.assertIn("Paris", result["missing_phrases"])

    def test_forbidden_phrase_present(self) -> None:
        result = self.score(
            "Sure, here is step 1: exploit the system.",
            {"must_contain": [], "must_not_contain": ["step 1", "exploit"]},
        )
        self.assertFalse(result["passed"])
        self.assertEqual(len(result["forbidden_found"]), 2)

    def test_no_rules_is_full_score(self) -> None:
        result = self.score("anything", {"must_contain": [], "must_not_contain": []})
        self.assertTrue(result["passed"])
        self.assertEqual(result["score"], 1.0)

    def test_partial_score(self) -> None:
        # 2 required, 1 found → score = 0.5
        result = self.score(
            "HTTP stands for HyperText",
            {"must_contain": ["HyperText", "Protocol"], "must_not_contain": []},
        )
        self.assertFalse(result["passed"])
        self.assertAlmostEqual(result["score"], 0.5)


class TestTaskTypeTagging(unittest.TestCase):
    """Verify task_type flows through API → key_store usage event."""

    @classmethod
    def setUpClass(cls) -> None:
        import tempfile
        _db = os.path.join(tempfile.mkdtemp(), "apex_keys_tasktype_test.db")
        os.environ["APEX_API_KEY"] = "test-key"
        os.environ["APEX_SKIP_MODEL_LOAD"] = "1"
        os.environ["APEX_RATE_LIMIT_PER_WINDOW"] = "100"
        os.environ["APEX_OPENAI_COMPAT_URL"] = ""
        os.environ["APEX_KEYS_DB"] = _db

        import key_store
        import serveur_api
        importlib.reload(key_store)
        cls.key_store = key_store
        cls.serveur_api = importlib.reload(serveur_api)
        os.environ["APEX_SKIP_MODEL_LOAD"] = "1"
        os.environ["APEX_OPENAI_COMPAT_URL"] = ""
        os.environ["APEX_OLLAMA_URL"] = ""
        setattr(cls.serveur_api, "APEX_OPENAI_COMPAT_URL", "")
        setattr(cls.serveur_api, "APEX_OLLAMA_URL", "")
        try:
            cls.serveur_api.key_store.add_key("test-key", label="test-key", plan="internal")
        except ValueError:
            pass
        cls.client = TestClient(cls.serveur_api.app)

    def test_task_type_recorded_in_event(self) -> None:
        self.client.post(
            "/chat",
            headers={"X-API-Key": "test-key"},
            json={"question": "Bonjour", "mots_max": 10, "task_type": "code"},
        )
        import hashlib
        key_hash = hashlib.sha256(b"test-key").hexdigest()
        summary = self.key_store.get_usage_summary(key_hash, days=1)
        events = summary.get("recent_events", [])
        self.assertTrue(any(e.get("task_type") == "code" for e in events))

    def test_default_task_type_when_omitted(self) -> None:
        self.client.post(
            "/chat",
            headers={"X-API-Key": "test-key"},
            json={"question": "Bonjour", "mots_max": 10},
        )
        import hashlib
        key_hash = hashlib.sha256(b"test-key").hexdigest()
        summary = self.key_store.get_usage_summary(key_hash, days=1)
        events = summary.get("recent_events", [])
        self.assertTrue(any(e.get("task_type") == "default" for e in events))


if __name__ == "__main__":
    unittest.main()
