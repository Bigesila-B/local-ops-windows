import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

import server


class ParsingTests(unittest.TestCase):
    def test_parse_etime(self):
        self.assertEqual(server.parse_etime("02:03"), 123)
        self.assertEqual(server.parse_etime("01:02:03"), 3723)
        self.assertEqual(server.parse_etime("2-01:02:03"), 176523)
        self.assertEqual(server.parse_etime("bad"), 0)

    def test_validate_port(self):
        self.assertEqual(server.validate_port("8791"), (8791, None))
        self.assertEqual(server.validate_port(None), (None, None))
        self.assertIsNotNone(server.validate_port(True)[1])
        self.assertIsNotNone(server.validate_port(70000)[1])


class ProjectDetectionTests(unittest.TestCase):
    def test_package_json_uses_lockfile_runner_and_framework_port(self):
        with tempfile.TemporaryDirectory() as td:
            with open(os.path.join(td, "package.json"), "w", encoding="utf-8") as f:
                json.dump({
                    "scripts": {"build": "vite build", "dev": "vite --host", "preview": "vite preview"},
                    "devDependencies": {"vite": "latest"},
                }, f)
            with open(os.path.join(td, "pnpm-lock.yaml"), "w", encoding="utf-8") as f:
                f.write("lockfileVersion: '9.0'\n")

            result, error = server.detect_project(td)

        self.assertIsNone(error)
        self.assertEqual([item["command"] for item in result["candidates"]],
                         ["pnpm run dev", "pnpm run preview"])
        self.assertEqual([item["port"] for item in result["candidates"]],
                         [5173, 4173])
        self.assertIn("package.json", result["files"])
        self.assertIn("pnpm-lock.yaml", result["files"])

    def test_explicit_script_port_wins_over_framework_default(self):
        with tempfile.TemporaryDirectory() as td:
            with open(os.path.join(td, "package.json"), "w", encoding="utf-8") as f:
                json.dump({"scripts": {"dev": "next dev --port 4321"},
                           "dependencies": {"next": "latest"}}, f)
            result, error = server.detect_project(td)

        self.assertIsNone(error)
        self.assertEqual(result["candidates"][0]["port"], 4321)

    def test_detects_positional_http_server_port_used_by_static_blogs(self):
        with tempfile.TemporaryDirectory() as td:
            with open(os.path.join(td, "package.json"), "w", encoding="utf-8") as f:
                json.dump({"scripts": {"dev": "python3 -m http.server 4173"}}, f)
            result, error = server.detect_project(td)

        self.assertIsNone(error)
        self.assertEqual(result["candidates"][0]["port"], 4173)

    def test_detects_django_and_static_site_fallback(self):
        with tempfile.TemporaryDirectory() as django_dir, tempfile.TemporaryDirectory() as static_dir:
            with open(os.path.join(django_dir, "manage.py"), "w", encoding="utf-8") as f:
                f.write("#!/usr/bin/env python3\n")
            with open(os.path.join(static_dir, "index.html"), "w", encoding="utf-8") as f:
                f.write("<!doctype html><title>Blog</title>")

            django, django_error = server.detect_project(django_dir)
            static, static_error = server.detect_project(static_dir)

        self.assertIsNone(django_error)
        self.assertEqual(django["candidates"][0]["command"], "python3 manage.py runserver")
        self.assertEqual(django["candidates"][0]["port"], 8000)
        self.assertIsNone(static_error)
        self.assertEqual(static["candidates"][0]["command"],
                         "python3 -m http.server 8000")

    def test_invalid_folder_returns_a_clear_error(self):
        result, error = server.detect_project("/path/that/does/not/exist")
        self.assertIsNone(result)
        self.assertIn("不存在", error)

    def test_framework_names_in_plain_strings_do_not_trigger_python_detection(self):
        with tempfile.TemporaryDirectory() as td:
            with open(os.path.join(td, "server.py"), "w", encoding="utf-8") as f:
                f.write('HELP = "try import streamlit or FastAPI( or Flask("\n')
            result, error = server.detect_project(td)

        self.assertIsNone(error)
        self.assertEqual(result["candidates"], [])

    def test_hexo_structure_needs_no_package_script(self):
        with tempfile.TemporaryDirectory() as td:
            with open(os.path.join(td, "_config.yml"), "w", encoding="utf-8") as f:
                f.write("title: Blog\n")
            os.mkdir(os.path.join(td, "source"))
            os.mkdir(os.path.join(td, "themes"))

            result, error = server.detect_project(td)

        self.assertIsNone(error)
        self.assertEqual(result["candidates"], [
            {"command": "hexo s", "label": "Hexo 本地服务",
             "source": "Hexo 项目结构", "port": 4000,
             "kind": "service", "detail": "等同于 hexo server"},
            {"command": "hexo cl", "label": "Hexo 清除缓存",
             "source": "Hexo 项目结构", "port": None,
             "kind": "task", "detail": "清除缓存和已生成文件，不启动服务"},
        ])

    def test_hexo_server_script_is_not_duplicated(self):
        with tempfile.TemporaryDirectory() as td:
            with open(os.path.join(td, "package.json"), "w", encoding="utf-8") as f:
                json.dump({"scripts": {"server": "hexo server"},
                           "dependencies": {"hexo": "latest"}}, f)

            result, error = server.detect_project(td)

        self.assertIsNone(error)
        self.assertEqual([item["command"] for item in result["candidates"]],
                         ["hexo s", "hexo cl"])


class ConfigTests(unittest.TestCase):
    def test_new_config_does_not_mutate_class_defaults(self):
        with tempfile.TemporaryDirectory() as td:
            original = json.loads(json.dumps(server.Config.DEFAULT))
            cfg = server.Config(os.path.join(td, "config.json"))
            cfg.update(lambda data: data["watchedKeywords"].append("node"))
            self.assertEqual(server.Config.DEFAULT, original)

    def test_atomic_write_keeps_previous_good_version_as_backup(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "config.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({**server.Config.DEFAULT,
                           "watchedKeywords": ["node"]}, f)
            cfg = server.Config(path)
            cfg.update(lambda data: data["watchedKeywords"].append("ffmpeg"))
            with open(path, "r", encoding="utf-8") as f:
                current = json.load(f)
            with open(path + ".bak", "r", encoding="utf-8") as f:
                backup = json.load(f)
            self.assertEqual(current["watchedKeywords"], ["node", "ffmpeg"])
            self.assertEqual(backup["watchedKeywords"], ["node"])
            self.assertEqual(oct(os.stat(path).st_mode & 0o777), "0o600")
            self.assertEqual(oct(os.stat(path + ".bak").st_mode & 0o777),
                             "0o600")

    def test_load_falls_back_to_backup(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "config.json")
            with open(path, "w", encoding="utf-8") as f:
                f.write("{")
            with open(path + ".bak", "w", encoding="utf-8") as f:
                json.dump({**server.Config.DEFAULT, "watchedKeywords": ["node"]}, f)
            cfg = server.Config(path)
            self.assertEqual(cfg.snapshot()["watchedKeywords"], ["node"])
            with open(path, "r", encoding="utf-8") as f:
                restored = json.load(f)
            self.assertEqual(restored["watchedKeywords"], ["node"])
            self.assertTrue(cfg.health_info()["recoveredFromBackup"])

    def test_legacy_schema_is_migrated_once_and_old_config_is_backup(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "config.json")
            legacy = {key: value for key, value in server.Config.DEFAULT.items()
                      if key != "schemaVersion"}
            legacy["watchedKeywords"] = ["ffmpeg"]
            with open(path, "w", encoding="utf-8") as f:
                json.dump(legacy, f)

            cfg = server.Config(path)
            self.assertEqual(cfg.snapshot()["schemaVersion"],
                             server.CURRENT_SCHEMA_VERSION)
            self.assertEqual(cfg.health_info()["migratedFromSchema"], 0)
            with open(path, "r", encoding="utf-8") as f:
                migrated = json.load(f)
            with open(path + ".bak", "r", encoding="utf-8") as f:
                previous = json.load(f)
            self.assertEqual(migrated["schemaVersion"], 1)
            self.assertNotIn("schemaVersion", previous)

            # 第二次读取已是当前 schema，不再改写备份。
            with open(path + ".bak", "rb") as f:
                previous_bytes = f.read()
            cfg2 = server.Config(path)
            self.assertIsNone(cfg2.health_info()["migratedFromSchema"])
            with open(path + ".bak", "rb") as f:
                self.assertEqual(f.read(), previous_bytes)

    def test_future_schema_is_not_silently_overwritten(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "config.json")
            future = {**server.Config.DEFAULT,
                      "schemaVersion": server.CURRENT_SCHEMA_VERSION + 1,
                      "watchedKeywords": ["future-data"]}
            with open(path, "w", encoding="utf-8") as f:
                json.dump(future, f)
            previous_backup = {**server.Config.DEFAULT,
                               "watchedKeywords": ["older-backup"]}
            with open(path + ".bak", "w", encoding="utf-8") as f:
                json.dump(previous_backup, f)
            cfg = server.Config(path)

            self.assertFalse(cfg.health_info()["writable"])
            with self.assertRaises(OSError):
                cfg.update(lambda data: data["watchedKeywords"].append("x"))
            with open(path, "r", encoding="utf-8") as f:
                self.assertEqual(json.load(f), future)
            with open(path + ".bak", "r", encoding="utf-8") as f:
                self.assertEqual(json.load(f), previous_backup)


class RuntimeStorageTests(unittest.TestCase):
    def test_runtime_override_requires_a_dedicated_absolute_directory(self):
        with mock.patch.dict(os.environ, {"TEST_CONSOLE_DIR": ""}):
            with self.assertRaises(RuntimeError):
                server.resolve_runtime_dir("TEST_CONSOLE_DIR", "/tmp/default")
        with mock.patch.dict(os.environ, {"TEST_CONSOLE_DIR": "relative"}):
            with self.assertRaises(RuntimeError):
                server.resolve_runtime_dir("TEST_CONSOLE_DIR", "/tmp/default")
        with mock.patch.dict(os.environ,
                             {"TEST_CONSOLE_DIR": os.path.expanduser("~")}):
            with self.assertRaises(RuntimeError):
                server.resolve_runtime_dir("TEST_CONSOLE_DIR", "/tmp/default")

    def test_first_run_copies_legacy_data_privately_without_deleting_source(self):
        with tempfile.TemporaryDirectory() as td:
            legacy = os.path.join(td, "project-data")
            target = os.path.join(td, "Application Support", "总控台")
            logs = os.path.join(td, "Logs", "总控台")
            os.makedirs(os.path.join(legacy, "icons"))
            os.makedirs(os.path.join(legacy, "logs"))
            with open(os.path.join(legacy, "config.json"), "w",
                      encoding="utf-8") as f:
                json.dump({**server.Config.DEFAULT,
                           "watchedKeywords": ["legacy"]}, f)
            with open(os.path.join(legacy, "icons", "deadbeef.png"), "wb") as f:
                f.write(b"icon")
            with open(os.path.join(legacy, "logs", "deadbeef.log"), "wb") as f:
                f.write(b"log")

            result = server.migrate_legacy_runtime_data(
                target, logs, legacy, False, False)

            self.assertEqual(result,
                             {"dataMigrated": True, "logsMigrated": True})
            self.assertTrue(os.path.isfile(os.path.join(target, "config.json")))
            with open(os.path.join(target, "icons", "deadbeef.png"), "rb") as f:
                self.assertEqual(f.read(), b"icon")
            with open(os.path.join(logs, "deadbeef.log"), "rb") as f:
                self.assertEqual(f.read(), b"log")
            self.assertTrue(os.path.isfile(os.path.join(legacy, "config.json")))
            self.assertEqual(oct(os.stat(target).st_mode & 0o777), "0o700")
            self.assertEqual(
                oct(os.stat(os.path.join(target, "config.json")).st_mode & 0o777),
                "0o600")

            # 已存在的目标绝不被旧项目目录二次覆盖。
            with open(os.path.join(legacy, "config.json"), "w",
                      encoding="utf-8") as f:
                json.dump({"changed": True}, f)
            again = server.migrate_legacy_runtime_data(
                target, logs, legacy, False, False)
            self.assertEqual(again,
                             {"dataMigrated": False, "logsMigrated": False})
            with open(os.path.join(target, "config.json"),
                      encoding="utf-8") as f:
                self.assertNotIn("changed", json.load(f))

    def test_explicit_overrides_never_trigger_legacy_migration(self):
        with tempfile.TemporaryDirectory() as td:
            legacy = os.path.join(td, "legacy")
            target = os.path.join(td, "custom-data")
            logs = os.path.join(td, "custom-logs")
            os.makedirs(os.path.join(legacy, "logs"))
            with open(os.path.join(legacy, "config.json"), "w") as f:
                f.write("{}")
            with open(os.path.join(legacy, "logs", "console.log"), "w") as f:
                f.write("log")

            result = server.migrate_legacy_runtime_data(
                target, logs, legacy, True, True)
            self.assertEqual(result,
                             {"dataMigrated": False, "logsMigrated": False})
            self.assertFalse(os.path.exists(target))
            self.assertFalse(os.path.exists(logs))

    def test_prepare_storage_cli_exits_without_starting_server(self):
        with tempfile.TemporaryDirectory() as td:
            target = os.path.join(td, "custom-data")
            logs = os.path.join(td, "custom-logs")
            env = dict(os.environ,
                       CONSOLE_DATA_DIR=target,
                       CONSOLE_LOG_DIR=logs)
            result = subprocess.run(
                [sys.executable, server.__file__, "--prepare-storage"],
                cwd=td, env=env, capture_output=True, text=True, timeout=5)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(os.path.isdir(target))
            self.assertTrue(os.path.isdir(os.path.join(target, "icons")))
            self.assertTrue(os.path.isdir(logs))
            # 显式 override 只准备私有目录，不复制项目内旧配置。
            self.assertFalse(os.path.exists(os.path.join(target, "config.json")))
            self.assertNotIn("总控台已启动", result.stdout + result.stderr)

    def test_prepare_storage_cli_fails_nonzero_when_directory_is_invalid(self):
        with tempfile.TemporaryDirectory() as td:
            blocker = os.path.join(td, "not-a-directory")
            with open(blocker, "w", encoding="utf-8") as f:
                f.write("block")
            env = dict(os.environ,
                       CONSOLE_DATA_DIR=os.path.join(blocker, "data"),
                       CONSOLE_LOG_DIR=os.path.join(td, "logs"))
            result = subprocess.run(
                [sys.executable, server.__file__, "--prepare-storage"],
                cwd=td, env=env, capture_output=True, text=True, timeout=5)
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn("总控台已启动", result.stdout + result.stderr)

    def test_app_launcher_redirects_output_only_after_storage_is_ready(self):
        with tempfile.TemporaryDirectory() as td:
            target = os.path.join(td, "custom-data")
            logs = os.path.join(td, "custom-logs")
            env = dict(os.environ,
                       CONSOLE_DATA_DIR=target,
                       CONSOLE_LOG_DIR=logs)
            script = (
                "import server; "
                "server.prepare_runtime_storage(); "
                "server.redirect_console_output(); "
                "print('launcher-log-ready', flush=True)"
            )
            result = subprocess.run(
                [sys.executable, "-c", script], cwd=server.BASE_DIR,
                env=env, capture_output=True, text=True, timeout=5)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, "")
            log_path = os.path.join(logs, "console.log")
            with open(log_path, encoding="utf-8") as f:
                self.assertEqual(f.read(), "launcher-log-ready\n")
            self.assertEqual(os.stat(log_path).st_mode & 0o777, 0o600)


class ProcessIdentityTests(unittest.TestCase):
    def test_random_marker_is_required_for_whole_process_group(self):
        app = {"id": "a", "lastPid": 42, "lastPgid": 42, "runToken": "right"}
        groups = {42: [42, 43]}
        snap = {
            42: {"uid": server.SELF_UID, "args": "bash console-run:right"},
            43: {"uid": server.SELF_UID, "args": "python service.py"},
        }
        with mock.patch.object(server, "ps_snapshot", return_value=snap):
            index, _, _ = server.managed_process_index([app], groups)
            self.assertEqual(index["a"], [42, 43])
            stale = dict(app, runToken="wrong")
            index, _, _ = server.managed_process_index([stale], groups)
            self.assertEqual(index["a"], [])

    def test_real_started_process_is_identified_and_stoppable(self):
        with tempfile.TemporaryDirectory() as td, \
                mock.patch.object(server, "LOGS_DIR", td):
            app = {"id": "deadbeef", "command": "sleep 20", "cwd": td}
            ok, error, proc, pgid, token = server.start_app(app)
            self.assertTrue(ok, error)
            tracked = dict(app, lastPid=proc.pid, lastPgid=pgid, runToken=token)
            try:
                time.sleep(0.15)
                self.assertIn(proc.pid, server.managed_pids(tracked))
                self.assertEqual(
                    server.managed_pids(dict(tracked, runToken="wrong")), [])
                self.assertTrue(server.stop_app(tracked))
                proc.wait(timeout=3)
            finally:
                if proc.poll() is None:
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except OSError:
                        pass
                    proc.wait(timeout=3)

    def test_verified_legacy_process_can_be_stopped_without_port_kill(self):
        app = {"id": "legacy", "lastPid": 999, "lastPgid": None,
               "runToken": None, "port": 8080, "cwd": "/tmp/project"}
        with mock.patch.object(server, "managed_pids", return_value=[]), \
                mock.patch.object(server, "legacy_managed_pid", return_value=999), \
                mock.patch.object(server.os, "kill") as stop:
            self.assertTrue(server.stop_app(app, {(999, 8080)}))
        stop.assert_called_once_with(999, signal.SIGTERM)

    def test_running_app_can_be_stopped_in_place_before_update(self):
        cfg = mock.Mock()
        app = {"id": "a", "runToken": "token"}
        with mock.patch.object(server, "app_alive_sign", return_value=True), \
                mock.patch.object(server, "stop_app_and_clear",
                                  return_value=(True, None)) as stop:
            ok, error, stopped = server.stop_app_for_update(cfg, app)

        self.assertTrue(ok, error)
        self.assertTrue(stopped)
        stop.assert_called_once_with(cfg, app, 5.0)

    def test_stopped_app_update_does_not_send_another_signal(self):
        cfg = mock.Mock()
        app = {"id": "a", "runToken": None}
        with mock.patch.object(server, "app_alive_sign", return_value=False), \
                mock.patch.object(server, "stop_app") as stop:
            ok, error, stopped = server.stop_app_for_update(cfg, app)

        self.assertTrue(ok, error)
        self.assertFalse(stopped)
        stop.assert_not_called()

    def test_task_exit_records_duration_and_unique_run_time(self):
        with tempfile.TemporaryDirectory() as td, \
                mock.patch.object(server, "LOGS_DIR", td):
            path = os.path.join(td, "config.json")
            app = {**server.Config.APP_DEFAULT, "id": "deadbeef",
                   "name": "任务", "kind": "task", "lastPid": 4321,
                   "lastPgid": 4321, "runToken": "token"}
            with open(path, "w", encoding="utf-8") as f:
                json.dump({**server.Config.DEFAULT, "apps": [app]}, f)
            cfg = server.Config(path)
            proc = mock.Mock(pid=4321)
            proc.wait.return_value = 0
            started_at = time.time() - 1.25

            thread = server.watch_app_exit(
                cfg, "deadbeef", proc, "token", started_at)
            thread.join(timeout=2)
            result = cfg.snapshot()["apps"][0]["lastExit"]

        self.assertEqual(result["code"], 0)
        self.assertAlmostEqual(result["durationSec"], 1.25, delta=0.2)
        self.assertEqual(result["startedAt"], int(started_at * 1000))

    def test_task_start_preserves_previous_completed_result(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "config.json")
            previous = {"code": 0, "at": 123, "durationSec": 0.4}
            task = {**server.Config.APP_DEFAULT, "id": "deadbeef",
                    "name": "任务", "kind": "task", "lastExit": previous}
            service = {**server.Config.APP_DEFAULT, "id": "feedface",
                       "name": "服务", "kind": "service", "lastExit": previous}
            with open(path, "w", encoding="utf-8") as f:
                json.dump({**server.Config.DEFAULT,
                           "apps": [task, service]}, f)
            cfg = server.Config(path)
            proc = mock.Mock(pid=4321)

            with mock.patch.object(server, "watch_app_exit"):
                self.assertTrue(server.persist_started_app(
                    cfg, "deadbeef", proc, 4321, "task-token"))
                self.assertTrue(server.persist_started_app(
                    cfg, "feedface", proc, 4321, "service-token"))
            apps = {app["id"]: app for app in cfg.snapshot()["apps"]}

        self.assertEqual(apps["deadbeef"]["lastExit"], previous)
        self.assertIsNone(apps["feedface"]["lastExit"])


class LaunchEnvironmentTests(unittest.TestCase):
    def test_headless_launch_path_includes_common_user_node_locations(self):
        with mock.patch.object(server.os.path, "expanduser", return_value="/Users/example"), \
                mock.patch.object(server.glob, "glob", side_effect=[
                    ["/Users/example/.nvm/versions/node/v22/bin"],
                    ["/Users/example/.fnm/node-versions/v20/installation/bin"],
                ]):
            env = server.build_launch_env("secret", {"PATH": "/usr/bin:/bin"})

        paths = env["PATH"].split(os.pathsep)
        self.assertIn("/Users/example/.local/bin", paths)
        self.assertIn("/usr/local/bin", paths)
        self.assertIn("/opt/homebrew/bin", paths)
        self.assertIn("/Users/example/.nvm/versions/node/v22/bin", paths)
        self.assertEqual(len(paths), len(set(paths)))
        self.assertEqual(env[server.RUN_TOKEN_ENV], "secret")

    def test_immediate_failure_message_uses_last_log_line(self):
        with tempfile.TemporaryDirectory() as td, \
                mock.patch.object(server, "LOGS_DIR", td):
            with open(os.path.join(td, "deadbeef.log"), "w", encoding="utf-8") as f:
                f.write("===== 启动于 now =====\nenv: node: No such file or directory\n")
            message = server.startup_failure_message("deadbeef", 127)

        self.assertIn("exit 127", message)
        self.assertIn("node: No such file", message)


class StateTests(unittest.TestCase):
    def test_legacy_listener_is_recognized_only_with_full_identity_match(self):
        app = {**server.Config.APP_DEFAULT, "id": "legacy", "name": "Legacy",
               "command": "python3 app.py", "cwd": "/tmp/project",
               "port": 8080, "lastPid": 999}
        proc = {999: {"uid": server.SELF_UID, "comm": "/usr/bin/python3",
                      "args": "python3 app.py", "etime": 42}}
        with mock.patch.object(
                server, "managed_process_index", return_value=({"legacy": []}, {}, {})), \
                mock.patch.object(server, "ps_snapshot", return_value=proc), \
                mock.patch.object(server, "lsof_cwds", return_value={999: "/tmp/project"}):
            row = server.build_apps({"apps": [app]}, {(999, 8080)})[0]
        self.assertTrue(row["running"])
        self.assertTrue(row["listening"])
        self.assertTrue(row["legacyManaged"])
        self.assertFalse(row["portOccupied"])

        with mock.patch.object(server, "ps_snapshot", return_value=proc), \
                mock.patch.object(server, "lsof_cwds", return_value={999: "/tmp/other"}):
            self.assertIsNone(server.legacy_managed_pid(app, {(999, 8080)}))

    def test_foreign_listener_is_conflict_not_running(self):
        app = {**server.Config.APP_DEFAULT, "id": "a", "name": "A",
               "command": "x", "port": 8080}
        with mock.patch.object(
                server, "managed_process_index", return_value=({"a": []}, {}, {})), \
                mock.patch.object(server, "ps_snapshot", return_value={
                    999: {"uid": server.SELF_UID, "comm": "/usr/bin/python3",
                          "args": "python3 other.py", "etime": 42},
                }), \
                mock.patch.object(server, "lsof_cwds", return_value={999: "/tmp/other"}):
            row = server.build_apps({"apps": [app]}, {(999, 8080)})[0]
        self.assertFalse(row["running"])
        self.assertTrue(row["portOccupied"])
        self.assertEqual(row["portOccupiedPid"], 999)
        self.assertEqual(row["portOwner"]["name"], "python3")
        self.assertEqual(row["portOwner"]["cwd"], "/tmp/other")
        self.assertTrue(row["portOwner"]["currentUser"])

    def test_duplicate_configured_ports_are_explicit(self):
        a = {**server.Config.APP_DEFAULT, "id": "a", "name": "A",
             "command": "x", "port": 8080}
        b = {**server.Config.APP_DEFAULT, "id": "b", "name": "B",
             "command": "y", "port": 8080}
        with mock.patch.object(
                server, "managed_process_index",
                return_value=({"a": [], "b": []}, {}, {})):
            rows = server.build_apps({"apps": [a, b]}, set())
        self.assertTrue(all(row["portConflict"] for row in rows))
        self.assertEqual(rows[0]["portConflictApps"], ["B"])
        self.assertEqual(server.find_port_conflicts({"apps": [a, b]}, 8080, "a"), [b])

    def test_watched_processes_are_current_user_only(self):
        snap = {
            10: {"uid": server.SELF_UID, "comm": "ffmpeg", "args": "ffmpeg -i a",
                 "cpu": 1.0, "mem": 2.0, "etime": 3},
            11: {"uid": server.SELF_UID + 1, "comm": "ffmpeg", "args": "ffmpeg -i b",
                 "cpu": 1.0, "mem": 2.0, "etime": 3},
        }
        with mock.patch.object(server, "ps_snapshot", return_value=snap):
            rows = server.build_watched(["ffmpeg"])
        self.assertEqual([row["pid"] for row in rows], [10])


class LogTests(unittest.TestCase):
    def test_rotation_and_tail_are_bounded(self):
        with tempfile.TemporaryDirectory() as td, \
                mock.patch.object(server, "LOGS_DIR", td):
            path = os.path.join(td, "a.log")
            with open(path, "wb") as f:
                f.write(b"one\ntwo\nthree\nfour\n")
            self.assertTrue(server.rotate_log_file(path, max_bytes=8, backups=2))
            with open(path, "ab") as f:
                f.write(b"five\nsix\n")
            self.assertEqual(server.read_log_tail("a", 3), "four\nfive\nsix")


class IconTests(unittest.TestCase):
    def test_all_allowed_icon_extensions_have_mime_types(self):
        for ext in server.ICON_EXTS:
            self.assertIn(ext, server.STATIC_TYPES)

    def test_favicon_urls_cannot_leave_the_managed_loopback_port(self):
        self.assertTrue(server.is_loopback_service_url(
            "http://127.0.0.1:4187/icon.png", 4187))
        self.assertTrue(server.is_loopback_service_url(
            "http://localhost:4187/icon.png", 4187))
        self.assertFalse(server.is_loopback_service_url(
            "https://127.0.0.1:4187/icon.png", 4187))
        self.assertFalse(server.is_loopback_service_url(
            "http://127.0.0.1:4188/icon.png", 4187))
        self.assertFalse(server.is_loopback_service_url(
            "http://example.com/icon.png", 4187))
        self.assertFalse(server.is_loopback_service_url(
            "http://127.0.0.1:4187@example.com/icon.png", 4187))

    def test_external_favicon_links_and_svg_payloads_are_rejected(self):
        png = b"\x89PNG\r\n\x1a\n" + b"payload"
        html = (b'<link rel="icon" href="https://example.com/track.svg">'
                b'<link rel="icon" href="/safe.png">')
        calls = []

        def fake_get(url, port, timeout=3, limit=262144):
            calls.append((url, port))
            if url.endswith("/"):
                return html, "text/html"
            if url.endswith("/safe.png"):
                return png, "image/png"
            return None, None

        with mock.patch.object(server, "http_get", side_effect=fake_get):
            data, ext = server.fetch_favicon(4187)

        self.assertEqual((data, ext), (png, "png"))
        self.assertNotIn(("https://example.com/track.svg", 4187), calls)
        self.assertIsNone(server.sniff_icon_bytes(
            b'<svg xmlns="http://www.w3.org/2000/svg"></svg>',
            "image/svg+xml"))


class ConsoleRestartTests(unittest.TestCase):
    def test_instance_discovery_is_limited_to_same_project(self):
        snap = {
            71001: {"uid": server.SELF_UID, "args": "python3 server.py",
                    "etime": 10},
            71002: {"uid": server.SELF_UID, "args": "python3 server.py",
                    "etime": 20},
            71003: {"uid": server.SELF_UID + 1, "args": "python3 server.py",
                    "etime": 30},
            71004: {"uid": server.SELF_UID, "args": "python3 server.py --launcher",
                    "etime": 40},
        }
        with mock.patch.object(server, "ps_snapshot", return_value=snap), \
                mock.patch.object(server, "lsof_cwds", return_value={
                    71001: server.BASE_DIR,
                    71002: "/tmp/different-project",
                    71004: server.BASE_DIR,
                }), \
                mock.patch.object(server, "scan_listeners", return_value={
                    (71001, 9600), (71004, 9601)}):
            found = server.find_console_instances()
        self.assertEqual([item["pid"] for item in found], [71001, 71004])
        self.assertEqual(found[0]["ports"], [9600])
        self.assertEqual(found[1]["ports"], [9601])

    def test_panel_restart_spawns_helper_before_shutdown(self):
        class FakeServer:
            def __init__(self):
                self.stopped = threading.Event()

            def shutdown(self):
                self.stopped.set()

        fake_server = FakeServer()
        fake_proc = mock.Mock(pid=72001)
        with mock.patch.object(server.subprocess, "Popen", return_value=fake_proc) as popen, \
                mock.patch.object(server.time, "sleep", return_value=None):
            helper_pid = server.schedule_console_restart(fake_server, 9603)
            self.assertTrue(fake_server.stopped.wait(1))
        self.assertEqual(helper_pid, 72001)
        command = popen.call_args.args[0]
        self.assertIn("--restart-helper", command)
        self.assertEqual(command[-1], "9603")

    def test_panel_stop_shuts_down_after_response_window(self):
        class FakeServer:
            def __init__(self):
                self.stopped = threading.Event()

            def shutdown(self):
                self.stopped.set()

        fake_server = FakeServer()
        with mock.patch.object(server.time, "sleep", return_value=None):
            server.schedule_console_stop(fake_server)
            self.assertTrue(fake_server.stopped.wait(1))


class DiagnoseTests(unittest.TestCase):
    def _run(self, app, log="", cfg_apps=None):
        cfg = {"apps": cfg_apps or [app]}
        with mock.patch.object(server, "read_log_tail", return_value=log):
            return server.diagnose_app(cfg, app)

    def test_missing_node_modules_suggests_lockfile_manager(self):
        with tempfile.TemporaryDirectory() as td:
            with open(os.path.join(td, "package.json"), "w", encoding="utf-8") as f:
                f.write('{"scripts": {"dev": "vite"}}')
            with open(os.path.join(td, "pnpm-lock.yaml"), "w", encoding="utf-8") as f:
                f.write("lockfileVersion: '9.0'\n")
            app = {"id": "aabbccdd", "name": "x", "cwd": td,
                   "command": "pnpm run dev", "port": 5173,
                   "lastExit": {"code": 2}}
            r = self._run(app)
        issue = next(i for i in r["issues"] if i["kind"] == "deps-missing")
        self.assertIn("pnpm install", issue["fix"])

    def test_cannot_find_module_from_log(self):
        app = {"id": "aabbccdd", "cwd": None, "command": "hexo s",
               "port": 4000, "lastExit": {"code": 2}}
        r = self._run(app, log="ERROR Cannot find module 'hexo' from '/x'")
        self.assertTrue(any(i["kind"] == "deps-missing" for i in r["issues"]))

    def test_missing_script_lists_available_scripts(self):
        with tempfile.TemporaryDirectory() as td:
            os.mkdir(os.path.join(td, "node_modules"))
            with open(os.path.join(td, "package.json"), "w", encoding="utf-8") as f:
                json.dump({"scripts": {"dev": "next dev", "build": "next build"}}, f)
            app = {"id": "aabbccdd", "cwd": td, "command": "npm run buld",
                   "port": None, "lastExit": {"code": 1}}
            r = self._run(app, log='npm error Missing script: "buld"')
        issue = next(i for i in r["issues"] if i["kind"] == "npm-script")
        self.assertIn("dev", issue["detail"])

    def test_exit_127_falls_back_to_command_not_found(self):
        app = {"id": "aabbccdd", "cwd": None, "command": "nooope",
               "port": None, "lastExit": {"code": 127}}
        r = self._run(app)
        self.assertTrue(any(i["kind"] == "not-found" for i in r["issues"]))

    def test_duplicate_port_config(self):
        a1 = {"id": "aabbccdd", "name": "A", "cwd": None, "command": "x",
              "port": 8080, "lastExit": {"code": 1}}
        a2 = {"id": "eeff0011", "name": "B", "cwd": None, "command": "y", "port": 8080}
        r = self._run(a1, cfg_apps=[a1, a2])
        self.assertTrue(any(i["kind"] == "port-dup" for i in r["issues"]))

    def test_clean_log_reports_no_match(self):
        app = {"id": "aabbccdd", "cwd": None, "command": "x",
               "port": None, "lastExit": {"code": 1}}
        r = self._run(app, log="some random output")
        self.assertEqual(r["issues"], [])
        self.assertIn("常见错误模式", r["summary"])


class ThemeTests(unittest.TestCase):
    def test_list_themes_reads_manifests(self):
        listed = server.list_themes()
        self.assertEqual([theme["id"] for theme in listed], ["apollo", "candy"])
        themes = {t["id"]: t for t in listed}
        self.assertIn("apollo", themes)
        self.assertIn("candy", themes)
        self.assertTrue(themes["apollo"]["colors"])
        self.assertEqual(themes["candy"]["name"], "Candy 彩色块")

    def test_config_defaults_ui_theme(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = server.Config(os.path.join(td, "config.json"))
            self.assertEqual(cfg.snapshot()["uiTheme"], "apollo")

    def test_config_preserves_ui_theme_and_scalars(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "config.json")
            cfg = server.Config(path)
            cfg.update(lambda d: d.__setitem__("uiTheme", "candy"))
            cfg2 = server.Config(path)
            snap = cfg2.snapshot()
            self.assertEqual(snap["uiTheme"], "candy")
            self.assertIsInstance(snap["apps"], list)


if __name__ == "__main__":
    unittest.main()
