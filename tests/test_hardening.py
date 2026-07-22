import http.client
import json
import os
import signal
import tempfile
import threading
import time
import unittest
from unittest import mock

import server


class HttpHarness:
    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory()
        path = os.path.join(self.tmp.name, "config.json")
        self.config_path = path
        self.cfg = server.Config(path)
        self.httpd = server.ConsoleServer(
            (server.HOST, 0), server.Handler, self.cfg, 0)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(
            target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=2)
        self.tmp.cleanup()

    def request(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection(server.HOST, self.port, timeout=4)
        request_headers = dict(headers or {})
        if body is not None and not isinstance(body, (bytes, bytearray)):
            body = body.encode("utf-8")
        conn.request(method, path, body=body, headers=request_headers)
        response = conn.getresponse()
        raw = response.read()
        result_headers = dict(response.getheaders())
        status = response.status
        conn.close()
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            payload = raw
        return status, payload, result_headers


class HttpSecurityTests(unittest.TestCase):
    def setUp(self):
        self.h = HttpHarness()

    def tearDown(self):
        self.h.close()

    def _browser_headers(self, cookie=None, origin=None):
        expected = "http://127.0.0.1:%d" % self.h.port
        headers = {
            "Content-Type": "application/json",
            "Origin": expected if origin is None else origin,
            "Sec-Fetch-Site": "same-origin",
        }
        if cookie:
            headers["Cookie"] = cookie
        return headers

    def _session_cookie(self):
        status, _, headers = self.h.request("GET", "/")
        self.assertEqual(status, 200)
        value = headers.get("Set-Cookie", "")
        self.assertIn("HttpOnly", value)
        self.assertIn("SameSite=Strict", value)
        return value.split(";", 1)[0]

    def test_dns_rebinding_host_is_rejected_without_setting_cookie(self):
        status, body, headers = self.h.request(
            "GET", "/api/state",
            headers={"Host": "attacker.example:%d" % self.h.port})
        self.assertEqual(status, 421)
        self.assertFalse(body["ok"])
        self.assertNotIn("Set-Cookie", headers)

    def test_cross_origin_browser_write_is_rejected_even_with_cookie(self):
        cookie = self._session_cookie()
        headers = self._browser_headers(cookie, "https://attacker.example")
        headers["Sec-Fetch-Site"] = "cross-site"
        status, body, _ = self.h.request(
            "POST", "/api/ui/theme", json.dumps({"theme": "candy"}), headers)
        self.assertEqual(status, 403)
        self.assertFalse(body["ok"])
        self.assertEqual(self.h.cfg.snapshot()["uiTheme"], "apollo")

    def test_same_origin_browser_write_requires_valid_http_only_session(self):
        status, _, _ = self.h.request(
            "POST", "/api/ui/theme", json.dumps({"theme": "candy"}),
            self._browser_headers())
        self.assertEqual(status, 403)

        cookie = self._session_cookie()
        status, body, _ = self.h.request(
            "POST", "/api/ui/theme", json.dumps({"theme": "candy"}),
            self._browser_headers(cookie))
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(self.h.cfg.snapshot()["uiTheme"], "candy")

    def test_simple_form_post_cannot_reach_bodyless_control_action(self):
        status, body, _ = self.h.request(
            "POST", "/api/console/stop", "x=1",
            {"Content-Type": "application/x-www-form-urlencoded"})
        self.assertEqual(status, 415)
        self.assertFalse(body["ok"])
        # The rejected request must not have scheduled shutdown.
        status, _, _ = self.h.request("GET", "/")
        self.assertEqual(status, 200)

    def test_headerless_local_cli_json_remains_compatible(self):
        status, body, _ = self.h.request(
            "POST", "/api/ui/theme", json.dumps({"theme": "candy"}),
            {"Content-Type": "application/json"})
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])

    def test_cors_preflight_is_explicitly_denied(self):
        status, _, headers = self.h.request(
            "OPTIONS", "/api/apps", headers={
                "Origin": "https://attacker.example",
                "Access-Control-Request-Method": "POST",
            })
        self.assertEqual(status, 403)
        self.assertNotIn("Access-Control-Allow-Origin", headers)


class DeliveryMetadataTests(unittest.TestCase):
    def setUp(self):
        self.h = HttpHarness()

    def tearDown(self):
        self.h.close()

    def test_state_exposes_version_schema_and_component_degradation(self):
        with mock.patch.object(server, "build_services",
                               side_effect=RuntimeError("lsof failed")), \
                mock.patch.object(server, "build_watched", return_value=[]), \
                mock.patch.object(server, "build_apps", return_value=[]), \
                mock.patch.object(server, "list_themes", return_value=[]):
            status, body, _ = self.h.request("GET", "/api/state")

        self.assertEqual(status, 200)
        self.assertEqual(body["version"], server.APP_VERSION)
        self.assertEqual(body["schemaVersion"],
                         server.CURRENT_SCHEMA_VERSION)
        self.assertTrue(body["degraded"])
        self.assertEqual(body["degradedReasons"][0]["component"], "services")

    def test_health_is_lightweight_and_reports_runtime_metadata(self):
        icons = os.path.join(self.h.tmp.name, "icons")
        logs = os.path.join(self.h.tmp.name, "logs")
        os.chmod(self.h.tmp.name, 0o700)
        os.mkdir(icons, 0o700)
        os.mkdir(logs, 0o700)
        os.chmod(icons, 0o700)
        os.chmod(logs, 0o700)
        with mock.patch.object(server, "DATA_DIR", self.h.tmp.name), \
                mock.patch.object(server, "ICONS_DIR", icons), \
                mock.patch.object(server, "LOGS_DIR", logs), \
                mock.patch.object(server, "CONFIG_PATH", self.h.config_path), \
                mock.patch.object(server, "build_services") as services:
            status, body, _ = self.h.request("GET", "/api/health")

        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["version"], server.APP_VERSION)
        self.assertEqual(body["schemaVersion"],
                         server.CURRENT_SCHEMA_VERSION)
        services.assert_not_called()


class OperationLockTests(unittest.TestCase):
    def setUp(self):
        self.h = HttpHarness()
        app = {**server.Config.APP_DEFAULT,
               "id": "deadbeef", "name": "Service", "command": "sleep 10",
               "kind": "service", "cwd": self.h.tmp.name}
        self.h.cfg.update(lambda data: data["apps"].append(app))

    def tearDown(self):
        self.h.close()

    def test_concurrent_start_is_rejected_before_second_process_is_spawned(self):
        entered = threading.Event()
        release = threading.Event()
        calls = []
        fake_proc = mock.Mock(pid=43123)
        fake_proc.poll.return_value = None

        def slow_start(app):
            calls.append(app["id"])
            entered.set()
            release.wait(2)
            return True, None, fake_proc, fake_proc.pid, "token"

        first_result = []

        def first_request():
            first_result.append(self.h.request(
                "POST", "/api/apps/deadbeef/start", "{}",
                {"Content-Type": "application/json"}))

        with mock.patch.object(server, "app_alive_sign", return_value=False), \
                mock.patch.object(server, "scan_listeners", return_value=set()), \
                mock.patch.object(server, "start_app", side_effect=slow_start), \
                mock.patch.object(server, "persist_started_app", return_value=True):
            thread = threading.Thread(target=first_request)
            thread.start()
            self.assertTrue(entered.wait(1))
            status, body, _ = self.h.request(
                "POST", "/api/apps/deadbeef/start", "{}",
                {"Content-Type": "application/json"})
            self.assertEqual(status, 409)
            self.assertFalse(body["ok"])
            release.set()
            thread.join(timeout=3)

        self.assertEqual(len(calls), 1)
        self.assertEqual(first_result[0][0], 200)

    def test_delete_keeps_config_when_verified_process_does_not_stop(self):
        with mock.patch.object(server, "app_running", return_value=True), \
                mock.patch.object(server, "stop_app_and_clear",
                                  return_value=(False, "应用仍在运行")):
            status, body, _ = self.h.request(
                "DELETE", "/api/apps/deadbeef")
        self.assertEqual(status, 409)
        self.assertFalse(body["ok"])
        self.assertIsNotNone(server.find_app(
            self.h.cfg.snapshot(), "deadbeef"))


class ProcessLifecycleHardeningTests(unittest.TestCase):
    def _config_with_app(self, directory, app):
        path = os.path.join(directory, "config.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({**server.Config.DEFAULT, "apps": [app]}, f)
        return server.Config(path)

    def test_manual_stop_waits_then_clears_without_recording_last_exit(self):
        with tempfile.TemporaryDirectory() as td, \
                mock.patch.object(server, "LOGS_DIR", td):
            base = {**server.Config.APP_DEFAULT, "id": "deadbeef",
                    "name": "Service", "command": "sleep 20", "cwd": td}
            cfg = self._config_with_app(td, base)
            ok, error, proc, pgid, token = server.start_app(base)
            self.assertTrue(ok, error)
            server.persist_started_app(cfg, base["id"], proc, pgid, token)
            tracked = server.find_app(cfg.snapshot(), base["id"])
            try:
                time.sleep(0.15)
                stopped, error = server.stop_app_and_clear(cfg, tracked, timeout=2)
                self.assertTrue(stopped, error)
                time.sleep(0.05)
                result = server.find_app(cfg.snapshot(), base["id"])
                self.assertIsNone(result["runToken"])
                self.assertIsNone(result["lastPid"])
                self.assertIsNone(result["lastExit"])
            finally:
                if server.stop_target_alive(
                        {"kind": "group", "id": pgid, "members": [proc.pid]}):
                    try:
                        os.killpg(pgid, signal.SIGKILL)
                    except OSError:
                        pass

    def test_sigterm_timeout_retains_runtime_identity_for_retry(self):
        command = (
            "python3 -c 'import signal,time; "
            "signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(20)'")
        with tempfile.TemporaryDirectory() as td, \
                mock.patch.object(server, "LOGS_DIR", td):
            base = {**server.Config.APP_DEFAULT, "id": "deadbeef",
                    "name": "Stubborn", "command": command, "cwd": td}
            cfg = self._config_with_app(td, base)
            ok, error, proc, pgid, token = server.start_app(base)
            self.assertTrue(ok, error)
            server.persist_started_app(cfg, base["id"], proc, pgid, token)
            tracked = server.find_app(cfg.snapshot(), base["id"])
            try:
                # Let the child install its SIGTERM handler before exercising
                # the timeout path (the real start endpoint probes for 250ms).
                time.sleep(0.6)
                stopped, error = server.stop_app_and_clear(
                    cfg, tracked, timeout=0.35)
                self.assertFalse(stopped)
                self.assertIn("保留管理状态", error)
                result = server.find_app(cfg.snapshot(), base["id"])
                self.assertEqual(result["runToken"], token)
                self.assertEqual(result["lastPgid"], pgid)
                self.assertTrue(server.stop_target_alive(
                    {"kind": "group", "id": pgid, "members": [proc.pid]}))
            finally:
                try:
                    os.killpg(pgid, signal.SIGKILL)
                except OSError:
                    pass


class SingleInstanceTests(unittest.TestCase):
    def test_project_lock_rejects_second_instance_until_release(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "console.lock")
            first = server.acquire_instance_lock(path)
            self.assertIsNotNone(first)
            try:
                self.assertIsNone(server.acquire_instance_lock(path))
            finally:
                server.release_instance_lock(first)
            third = server.acquire_instance_lock(path)
            self.assertIsNotNone(third)
            server.release_instance_lock(third)


if __name__ == "__main__":
    unittest.main()
