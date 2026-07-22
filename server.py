#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""总控台后端（单文件，仅 Python 3 标准库）。

本地服务监控 + 快速启动台：
    python3 server.py  →  绑定 127.0.0.1，端口 9600 起（被占 +1，最多 10 个）
API 契约与实现要点见 AGENTS.md。
"""

import glob
import fcntl
import functools
import errno
import json
import logging
import os
import re
import secrets
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
VERSION_PATH = os.path.join(BASE_DIR, "VERSION")
LEGACY_DATA_DIR = os.path.join(BASE_DIR, "data")
DEFAULT_DATA_DIR = os.path.expanduser(
    "~/Library/Application Support/总控台")
DEFAULT_LOGS_DIR = os.path.expanduser("~/Library/Logs/总控台")


def resolve_runtime_dir(name, default):
    """解析专用运行目录，拒绝空值、相对路径和过宽目标。"""
    if name not in os.environ:
        return os.path.abspath(default), False
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        raise RuntimeError("%s 不能为空" % name)
    expanded = os.path.expanduser(raw)
    if not os.path.isabs(expanded):
        raise RuntimeError("%s 必须是绝对路径" % name)
    path = os.path.abspath(expanded)
    forbidden = {os.path.abspath(os.sep), os.path.abspath(os.path.expanduser("~")),
                 os.path.abspath(BASE_DIR)}
    if path in forbidden:
        raise RuntimeError("%s 必须指向专用子目录" % name)
    return path, True


DATA_DIR, DATA_DIR_OVERRIDDEN = resolve_runtime_dir(
    "CONSOLE_DATA_DIR", DEFAULT_DATA_DIR)
ICONS_DIR = os.path.join(DATA_DIR, "icons")
LOGS_DIR, LOGS_DIR_OVERRIDDEN = resolve_runtime_dir(
    "CONSOLE_LOG_DIR", DEFAULT_LOGS_DIR)
STATIC_DIR = os.path.join(BASE_DIR, "static")
THEMES_DIR = os.path.join(STATIC_DIR, "themes")
CONFIG_PATH = os.path.join(DATA_DIR, "config.json")
INSTANCE_LOCK_PATH = os.path.join(DATA_DIR, "console.lock")

CURRENT_SCHEMA_VERSION = 1


def read_project_version(path=VERSION_PATH):
    """读取根目录 VERSION。失败时保持服务可诊断，但标记为降级。"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            value = f.read(128).strip()
        if not re.fullmatch(
                r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)"
                r"(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?", value):
            raise ValueError("VERSION 不是合法的 SemVer")
        return value, None
    except (OSError, UnicodeError, ValueError) as e:
        return "0.0.0+unknown", str(e)


APP_VERSION, VERSION_LOAD_ERROR = read_project_version()

HOST = "127.0.0.1"
PORT_START = 9600
PORT_TRIES = 10
SUBPROCESS_TIMEOUT = 5          # lsof/ps 等子进程超时（秒）
MAX_ICON_BYTES = 5 * 1024 * 1024
MAX_JSON_BYTES = 1 * 1024 * 1024
MAX_DETECT_FILE_BYTES = 2 * 1024 * 1024
MAX_LOG_BYTES = 10 * 1024 * 1024
LOG_BACKUPS = 3
LOG_MAINTENANCE_SEC = 30
STARTUP_PROBE_SEC = 0.25
APP_STOP_TIMEOUT_SEC = 5.0
RUN_TOKEN_ENV = "CONSOLE_RUN_TOKEN"
RUN_TOKEN_ARG_PREFIX = "console-run:"

SELF_PID = os.getpid()
SELF_UID = os.getuid()
ICON_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".ico")
LOG = logging.getLogger("console")
LOG_LOCK = threading.RLock()
MANUAL_STOP_LOCK = threading.RLock()
MANUAL_STOP_TOKENS = set()

STATIC_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".otf": "font/otf",
    ".woff2": "font/woff2",
}

PLACEHOLDER_HTML = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>总控台</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
body{font-family:-apple-system,BlinkMacSystemFont,"PingFang SC",sans-serif;display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0;background:#f5f5f7;color:#1d1d1f}
.card{background:#fff;border:1px solid rgba(0,0,0,.06);border-radius:14px;padding:36px 44px;box-shadow:0 8px 30px rgba(0,0,0,.08);max-width:540px;text-align:center}
h1{font-size:20px;margin:0 0 14px}p{color:#6e6e73;font-size:14px;line-height:1.8;margin:6px 0}
code{background:#f5f5f7;border:1px solid rgba(0,0,0,.05);border-radius:6px;padding:2px 7px;font-family:ui-monospace,Menlo,monospace;font-size:13px}
</style></head>
<body><div class="card">
<h1>🖥 总控台后端运行中</h1>
<p>前端文件 <code>static/index.html</code> 尚未提供，界面暂不可用。</p>
<p>API 已就绪：<code>GET /api/state</code></p>
</div></body></html>"""

APP_ROUTE_RE = re.compile(
    r"^/api/apps/([0-9a-fA-F]{8})(?:/(start|stop|restart|icon|logs|favicon|diagnose))?$")


# ---------------------------------------------------------------- 运行目录

def _ensure_private_dir(path):
    if os.path.islink(path):
        raise OSError("私有运行目录不能是符号链接: %s" % path)
    os.makedirs(path, mode=0o700, exist_ok=True)
    if os.path.islink(path) or not os.path.isdir(path):
        raise OSError("私有运行路径不是安全目录: %s" % path)
    try:
        os.chmod(path, 0o700)
    except OSError:
        LOG.warning("无法收紧目录权限: %s", path)


def _copy_private_regular_file(source, target):
    """不跟随符号链接地复制普通文件，目标权限固定为 0600。"""
    try:
        source_stat = os.lstat(source)
    except OSError:
        return False
    if not stat.S_ISREG(source_stat.st_mode):
        return False
    source_flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    source_fd = os.open(source, source_flags)
    try:
        target_fd = os.open(
            target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(os.dup(source_fd), "rb") as src, \
                    os.fdopen(target_fd, "wb") as dst:
                target_fd = -1
                shutil.copyfileobj(src, dst, length=1024 * 1024)
                dst.flush()
                os.fsync(dst.fileno())
        finally:
            if target_fd >= 0:
                os.close(target_fd)
    finally:
        os.close(source_fd)
    os.chmod(target, 0o600)
    return True


def _install_migrated_directory(target, populate):
    """在目标不存在时原子安装一份迁移副本。"""
    if os.path.lexists(target):
        return False
    parent = os.path.dirname(target) or "."
    # parent 可能是用户共用的 ~/Library/Application Support，
    # 只确保存在，不擅自改它的现有权限。
    os.makedirs(parent, mode=0o700, exist_ok=True)
    staging = tempfile.mkdtemp(prefix=".console-migration-", dir=parent)
    installed = False
    try:
        os.chmod(staging, 0o700)
        populate(staging)
        try:
            os.rename(staging, target)
            installed = True
        except OSError as e:
            # 另一个同时启动的实例可能已经完成迁移。
            if not os.path.lexists(target) or e.errno not in (
                    errno.EEXIST, errno.ENOTEMPTY):
                raise
        return installed
    finally:
        if not installed and os.path.isdir(staging):
            shutil.rmtree(staging)


def migrate_legacy_runtime_data(
        data_dir=DATA_DIR, logs_dir=LOGS_DIR,
        legacy_data_dir=LEGACY_DATA_DIR,
        data_overridden=DATA_DIR_OVERRIDDEN,
        logs_overridden=LOGS_DIR_OVERRIDDEN):
    """首次运行时将项目内旧数据复制到 macOS 用户目录。

    只在对应目标完全不存在且没有显式环境变量覆盖时执行。
    旧文件不会被删除或改权限。
    """
    result = {"dataMigrated": False, "logsMigrated": False}
    legacy_data_dir = os.path.abspath(legacy_data_dir)
    data_dir = os.path.abspath(data_dir)
    logs_dir = os.path.abspath(logs_dir)

    if (not data_overridden and data_dir != legacy_data_dir
            and os.path.isdir(legacy_data_dir)
            and not os.path.lexists(data_dir)):
        def populate_data(staging):
            for name in ("config.json", "config.json.bak"):
                _copy_private_regular_file(
                    os.path.join(legacy_data_dir, name),
                    os.path.join(staging, name))
            source_icons = os.path.join(legacy_data_dir, "icons")
            if os.path.isdir(source_icons) and not os.path.islink(source_icons):
                target_icons = os.path.join(staging, "icons")
                os.mkdir(target_icons, 0o700)
                for name in os.listdir(source_icons):
                    if os.path.basename(name) != name:
                        continue
                    _copy_private_regular_file(
                        os.path.join(source_icons, name),
                        os.path.join(target_icons, name))

        result["dataMigrated"] = _install_migrated_directory(
            data_dir, populate_data)

    legacy_logs = os.path.join(legacy_data_dir, "logs")
    if (not logs_overridden and logs_dir != legacy_logs
            and os.path.isdir(legacy_logs) and not os.path.islink(legacy_logs)
            and not os.path.lexists(logs_dir)):
        def populate_logs(staging):
            for name in os.listdir(legacy_logs):
                if os.path.basename(name) != name:
                    continue
                _copy_private_regular_file(
                    os.path.join(legacy_logs, name),
                    os.path.join(staging, name))

        result["logsMigrated"] = _install_migrated_directory(
            logs_dir, populate_logs)
    return result


def prepare_runtime_storage():
    migration = migrate_legacy_runtime_data()
    for private_dir in (DATA_DIR, ICONS_DIR, LOGS_DIR):
        _ensure_private_dir(private_dir)
    for path in (CONFIG_PATH, CONFIG_PATH + ".bak", INSTANCE_LOCK_PATH):
        try:
            if stat.S_ISREG(os.lstat(path).st_mode):
                os.chmod(path, 0o600)
        except OSError:
            pass
    for directory in (ICONS_DIR, LOGS_DIR):
        try:
            entries = os.scandir(directory)
        except OSError:
            continue
        with entries:
            for entry in entries:
                try:
                    if entry.is_file(follow_symlinks=False):
                        os.chmod(entry.path, 0o600)
                except OSError:
                    LOG.warning("无法收紧文件权限: %s", entry.path)
    return migration


def write_private_bytes(path, payload):
    """以 0600 权限写入用户数据文件。"""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(payload)
        f.flush()
        os.fsync(f.fileno())
    os.chmod(path, 0o600)


# ---------------------------------------------------------------- 配置


class ConfigSchemaError(ValueError):
    pass


class FutureConfigSchemaError(ConfigSchemaError):
    pass


def migrate_config_v0_to_v1(raw):
    """旧配置没有 schemaVersion；v1 只建立显式版本基线。"""
    migrated = dict(raw)
    migrated["schemaVersion"] = 1
    return migrated


CONFIG_MIGRATIONS = {0: migrate_config_v0_to_v1}


def migrate_config(raw):
    """将任意已支持的旧 schema 逐版幂等迁移到当前版本。"""
    if not isinstance(raw, dict):
        raise ConfigSchemaError("配置根节点必须是 JSON 对象")
    version = raw.get("schemaVersion", 0)
    if type(version) is not int or version < 0:
        raise ConfigSchemaError("schemaVersion 必须是非负整数")
    if version > CURRENT_SCHEMA_VERSION:
        raise FutureConfigSchemaError(
            "配置 schemaVersion=%d 新于当前程序支持的 %d" %
            (version, CURRENT_SCHEMA_VERSION))
    source_version = version
    migrated = json.loads(json.dumps(raw, ensure_ascii=False))
    while version < CURRENT_SCHEMA_VERSION:
        migration = CONFIG_MIGRATIONS.get(version)
        if migration is None:
            raise ConfigSchemaError("缺少 schemaVersion=%d 的迁移器" % version)
        migrated = migration(migrated)
        next_version = migrated.get("schemaVersion")
        if next_version != version + 1:
            raise ConfigSchemaError("配置迁移器未正确递增 schemaVersion")
        version = next_version
    return migrated, source_version


class Config:
    """配置读写：显式 schema 迁移 + 原子写 + 上一份良好备份。"""

    DEFAULT = {"schemaVersion": CURRENT_SCHEMA_VERSION,
               "apps": [], "hidden": [], "pinned": [], "promoted": [],
               "watchedKeywords": [], "uiTheme": "apollo"}
    APP_DEFAULT = {"id": None, "name": "", "command": "", "cwd": None,
                   "port": None, "emoji": None, "glyph": None, "icon": None,
                   "favicon": None, "kind": "service", "lastPid": None,
                   "lastPgid": None, "runToken": None,
                   "lastExit": None, "createdAt": 0}

    def __init__(self, path):
        self._lock = threading.RLock()
        self._path = path
        self._writable = True
        self._recovered_from_backup = False
        self._migration_from = None
        self._health_issues = []
        self._data = self._load()

    @staticmethod
    def _payload(data):
        return json.dumps(data, ensure_ascii=False, indent=2) + "\n"

    @classmethod
    def _normalize(cls, raw):
        data = {"schemaVersion": CURRENT_SCHEMA_VERSION}
        for key, default in cls.DEFAULT.items():
            if key == "schemaVersion":
                continue
            value = raw.get(key)
            if isinstance(value, type(default)):
                data[key] = (json.loads(json.dumps(value, ensure_ascii=False))
                             if isinstance(value, (list, dict)) else value)
            else:
                data[key] = list(default) if isinstance(default, list) else default
        apps = []
        for item in data["apps"]:
            if not isinstance(item, dict) or not item.get("id"):
                continue
            app = dict(cls.APP_DEFAULT)
            for key in app:
                if key in item:
                    app[key] = item[key]
            apps.append(app)
        data["apps"] = apps
        return data

    def _load(self):
        paths = (self._path, self._path + ".bak")
        found_candidate = False
        for index, path in enumerate(paths):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                migrated, source_version = migrate_config(raw)
                data = self._normalize(migrated)
                if index:
                    self._recovered_from_backup = True
                    LOG.warning("主配置不可读，已从备份恢复: %s", path)
                if source_version < CURRENT_SCHEMA_VERSION:
                    self._migration_from = source_version
                self._persist_loaded_state(
                    data, raw, source_index=index,
                    source_version=source_version)
                return data
            except FileNotFoundError:
                continue
            except FutureConfigSchemaError as e:
                # 回退到旧程序时绝不用旧 .bak 覆盖更新 schema 的主文件。
                found_candidate = True
                self._health_issues.append(str(e))
                LOG.error("拒绝降级读取配置: %s", path)
                break
            except (OSError, UnicodeError, json.JSONDecodeError,
                    ConfigSchemaError, TypeError, ValueError):
                found_candidate = True
                LOG.exception("读取配置失败: %s", path)
        data = self._normalize(self.DEFAULT)
        if found_candidate:
            # 配置和备份都不可用时，展示空状态但禁止写入，
            # 避免一次 UI 操作就把尚可人工恢复的文件覆盖。
            self._writable = False
            self._health_issues.append(
                "主配置与备份均不可读，已进入只读保护状态")
            return data
        try:
            self._write_atomic(self._path, self._payload(data))
        except OSError as e:
            self._writable = False
            self._health_issues.append("无法创建配置文件: %s" % e)
        return data

    def _persist_loaded_state(self, data, raw, source_index, source_version):
        """将已恢复/迁移的配置落回主文件，不破坏良好备份。"""
        needs_migration = source_version < CURRENT_SCHEMA_VERSION
        if not source_index and not needs_migration:
            return
        try:
            if not source_index and needs_migration:
                # 迁移前的配置是上一份良好版本。
                self._write_atomic(self._path + ".bak", self._payload(raw))
            # 从 .bak 恢复时只修复主文件，保留已验证的备份。
            self._write_atomic(self._path, self._payload(data))
        except OSError as e:
            self._writable = False
            self._health_issues.append("配置恢复/迁移落盘失败: %s" % e)
            LOG.exception("配置恢复/迁移落盘失败")

    def snapshot(self):
        """返回配置的深拷贝（数据均为 JSON 可序列化）。"""
        with self._lock:
            return json.loads(json.dumps(self._data, ensure_ascii=False))

    def health_info(self):
        with self._lock:
            return {
                "writable": self._writable,
                "recoveredFromBackup": self._recovered_from_backup,
                "migratedFromSchema": self._migration_from,
                "issues": list(self._health_issues),
            }

    def update(self, fn):
        """在锁内执行 fn(self._data) 修改配置，随后原子落盘，返回 fn 的返回值。"""
        with self._lock:
            if not self._writable:
                raise OSError("配置处于只读保护状态，请先恢复配置或权限")
            previous = json.loads(json.dumps(self._data, ensure_ascii=False))
            try:
                result = fn(self._data)
                payload = self._payload(self._data)
                previous_payload = self._payload(previous)
                # 先保存上一份良好内容，再替换主文件。
                self._write_atomic(self._path + ".bak", previous_payload)
                self._write_atomic(self._path, payload)
                return result
            except Exception:
                self._data = previous
                raise

    @staticmethod
    def _write_atomic(path, payload):
        _ensure_private_dir(os.path.dirname(path) or ".")
        tmp = path + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        os.chmod(path, 0o600)


def acquire_instance_lock(path=INSTANCE_LOCK_PATH):
    """Acquire the per-project process lock and keep its file object alive.

    Port fallback alone is not a single-instance guarantee: two servers on
    :9600/:9601 would still update the same config.  flock ties exclusivity to
    this data directory and is released automatically if the process crashes.
    """
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, mode=0o700, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    lock_file = os.fdopen(fd, "r+", encoding="ascii")
    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as e:
        lock_file.close()
        if e.errno in (errno.EACCES, errno.EAGAIN):
            return None
        raise
    try:
        os.fchmod(lock_file.fileno(), 0o600)
        lock_file.seek(0)
        lock_file.truncate()
        lock_file.write("%d\n" % SELF_PID)
        lock_file.flush()
        os.fsync(lock_file.fileno())
    except OSError:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        lock_file.close()
        raise
    return lock_file


def release_instance_lock(lock_file):
    if lock_file is None:
        return
    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
    finally:
        lock_file.close()


# ---------------------------------------------------------------- 子进程与解析

def run_cmd(args, timeout=SUBPROCESS_TIMEOUT):
    """运行命令并返回 stdout；任何异常/超时都返回空串，绝不上抛。"""
    try:
        r = subprocess.run(args, capture_output=True, text=True,
                           errors="replace", timeout=timeout)
        return r.stdout or ""
    except Exception:
        LOG.exception("命令执行失败: %r", args)
        return ""


def parse_etime(s):
    """ps 的 etime：[[dd-]hh:]mm:ss → 秒。异常返回 0。"""
    try:
        s = s.strip()
        days = 0
        if "-" in s:
            d, s = s.split("-", 1)
            days = int(d)
        parts = [int(p) for p in s.split(":")]
        if len(parts) == 2:
            hours, minutes, secs = 0, parts[0], parts[1]
        elif len(parts) == 3:
            hours, minutes, secs = parts
        else:
            return 0
        return days * 86400 + hours * 3600 + minutes * 60 + secs
    except Exception:
        return 0


def _to_float(tok, default=0.0):
    try:
        return float(tok)
    except (TypeError, ValueError):
        return default


def scan_listeners():
    """lsof -iTCP -sTCP:LISTEN -P -n → {(pid, port), ...}，按 (pid,port) 去重。"""
    out = run_cmd(["lsof", "-iTCP", "-sTCP:LISTEN", "-P", "-n"])
    found = {}
    for line in out.splitlines():
        if not line or line.startswith("COMMAND"):
            continue
        parts = line.split()
        if len(parts) < 9:
            continue
        try:
            pid = int(parts[1])
        except ValueError:
            continue
        # NAME 列形如 *:8791 / 127.0.0.1:8080 / [::1]:8765，末尾可能跟 "(LISTEN)"
        port = None
        for tok in reversed(parts):
            m = re.search(r":(\d+)$", tok)
            if m:
                port = int(m.group(1))
                break
        if port is None:
            continue
        found[(pid, port)] = True
    return found


def ps_snapshot(pids=None, with_uid=True):
    """批量进程信息 → {pid: {"uid","comm","args","cpu","mem","etime"}}。

    pids=None 表示全部进程（ps -ax）。解析：左边固定列 pid[/uid]/etime/cpu/mem，
    其余部分（可含空格）即 comm；args 单独一次 ps 取。
    注意：不能用 `comm=` 抑制表头——macOS ps 会把空表头列压到 16 字节截断
    内容；保留表头后解析时跳过表头行即可（首列非数字的行）。
    """
    base = ["ps"]
    if pids is None:
        base.append("-ax")
    else:
        pids = [int(p) for p in pids]
        if not pids:
            return {}
        base += ["-p", ",".join(str(p) for p in pids)]
    # comm 必须放在最后一列：macOS ps 只保证最后一列不被定宽截断
    # （comm 在中间列时会被压成约 16 字节，长路径被砍断）。
    fields = ["pid"] + (["uid"] if with_uid else []) + \
             ["etime", "%cpu", "%mem", "comm"]
    out1 = run_cmd(base + ["-o", ",".join(fields)])
    out2 = run_cmd(base + ["-o", "pid,args"])

    snap = {}
    fixed = 5 if with_uid else 4  # pid [uid] etime cpu mem 之后的都是 comm
    for line in out1.splitlines():
        toks = line.split()
        if len(toks) < fixed + 1:
            continue
        try:
            pid = int(toks[0])
        except ValueError:
            continue  # 表头行
        i = 1
        entry = {"args": ""}
        if with_uid:
            try:
                entry["uid"] = int(toks[1])
            except ValueError:
                entry["uid"] = -1
            i = 2
        entry["etime"] = parse_etime(toks[i])
        entry["cpu"] = _to_float(toks[i + 1])
        entry["mem"] = _to_float(toks[i + 2])
        entry["comm"] = " ".join(toks[i + 3:])
        snap[pid] = entry
    for line in out2.splitlines():
        toks = line.split(None, 1)
        if not toks:
            continue
        try:
            pid = int(toks[0])
        except ValueError:
            continue
        if pid in snap:
            snap[pid]["args"] = toks[1] if len(toks) > 1 else ""
    return snap


def lsof_cwds(pids):
    """lsof -a -p <pids> -d cwd -Fn → {pid: cwd}。"""
    pids = [int(p) for p in pids]
    if not pids:
        return {}
    out = run_cmd(["lsof", "-a", "-p", ",".join(str(p) for p in pids),
                   "-d", "cwd", "-Fn"])
    result = {}
    cur = None
    for line in out.splitlines():
        if line.startswith("p"):
            try:
                cur = int(line[1:])
            except ValueError:
                cur = None
        elif line.startswith("n") and cur is not None:
            result[cur] = line[1:]
    return result


def pid_alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except PermissionError:
        return True
    except (OSError, ValueError, TypeError):
        return False


# ---------------------------------------------------------------- 状态构建

SYSTEM_PATH_PREFIXES = ("/usr/libexec/", "/usr/sbin/", "/sbin/", "/System/", "/usr/lib/")

# 开发服务关键词：命中 name/args 时优先归为 "mine"（覆盖 .app 规则，
# 例如 ollama 守护进程在 Ollama.app 内、Docker 在 Docker.app 内）
DEV_KEYWORDS = (
    "python", "node", "ruby", "php", "nginx", "caddy", "postgres",
    "mysql", "redis", "mongo", "ollama", "docker", "deno", "bun",
    "uvicorn", "gunicorn", "hugo", "vite", "streamlit", "jupyter",
    "ngrok", "frp", "code-server", "java",
)


def classify_group(key, name, comm, args, cwd, promoted):
    if key in promoted:
        return "mine"
    text = name.lower()
    if any(k in text for k in DEV_KEYWORDS):
        return "mine"
    if ".app/Contents/" in comm or ".app/Contents/" in args:
        return "background"
    if comm.startswith(SYSTEM_PATH_PREFIXES):
        return "background"
    if "/Library/Containers/" in comm or "/Library/Containers/" in (cwd or ""):
        return "background"
    return "mine"


HOME_DIR = os.path.expanduser("~")


def project_name(cwd):
    """从工作目录推断项目名（最后一段目录名），无有效 cwd 时返回 None。"""
    if not cwd:
        return None
    cwd = cwd.rstrip("/")
    if not cwd or cwd == "/" or cwd == HOME_DIR:
        return None
    return os.path.basename(cwd) or None


def build_services(cfg):
    """返回 (services, listeners)。只含当前用户进程，排除控制台自身。"""
    listeners = scan_listeners()
    snap = ps_snapshot({pid for pid, _ in listeners}, with_uid=True)
    mine_pids = [pid for pid, _ in listeners
                 if pid != SELF_PID and pid in snap
                 and snap[pid].get("uid") == SELF_UID]
    cwds = lsof_cwds(mine_pids)

    hidden = set(cfg.get("hidden") or [])
    pinned = set(cfg.get("pinned") or [])
    promoted = set(cfg.get("promoted") or [])
    # 只关联唯一配置的端口；重复端口不猜测属于哪张应用卡。
    apps_by_port = {}
    for candidate in cfg.get("apps") or []:
        if candidate.get("port"):
            apps_by_port.setdefault(candidate["port"], []).append(candidate)
    app_by_port = {port: items[0] for port, items in apps_by_port.items()
                   if len(items) == 1}

    services = []
    for pid, port in sorted(listeners, key=lambda x: (x[1], x[0])):
        if pid == SELF_PID:
            continue
        info = snap.get(pid)
        if not info or info.get("uid") != SELF_UID:
            continue
        comm = info.get("comm") or ""
        args = info.get("args") or comm
        name = os.path.basename(comm) if comm else "?"
        key = "%s:%d" % (name, port)
        cwd = cwds.get(pid)
        app = app_by_port.get(port)
        services.append({
            "key": key, "pid": pid, "name": name, "port": port,
            "cwd": cwd, "project": project_name(cwd), "cmd": args,
            "cpu": info["cpu"], "mem": info["mem"], "uptimeSec": info["etime"],
            "group": classify_group(key, name, comm, args, cwd, promoted),
            "pinned": key in pinned, "hidden": key in hidden,
            "promoted": key in promoted,
            "appId": app["id"] if app else None,
            "appName": app["name"] if app else None,
        })
    return services, listeners


def build_watched(keywords):
    """关注进程：args 小写包含关键字即命中；排除自身及 ps/lsof。"""
    keywords = [k for k in (keywords or []) if isinstance(k, str) and k.strip()]
    if not keywords:
        return []
    snap = ps_snapshot(None, with_uid=True)
    result = []
    for kw in keywords:
        kl = kw.lower()
        for pid, info in snap.items():
            if pid == SELF_PID or info.get("uid") != SELF_UID:
                continue
            name = os.path.basename(info.get("comm") or "") or "?"
            if name in ("ps", "lsof"):
                continue
            args = info.get("args") or ""
            if kl not in args.lower():
                continue
            result.append({"pid": pid, "name": name, "cmd": args,
                           "cpu": info["cpu"], "mem": info["mem"],
                           "uptimeSec": info["etime"], "keyword": kw})
    return result


def pgid_members_map():
    """ps -axo pid=,pgid= → {pgid: [pid, ...]}。
    进程退出后其子孙仍保留原 pgid（被 launchd 收养也不变），
    因此按 pgid 能找到「脚本把服务放后台后自己退出」的存活成员。"""
    groups = {}
    for line in run_cmd(["ps", "-axo", "pid=,pgid="]).splitlines():
        parts = line.split()
        if len(parts) != 2:
            continue
        try:
            pid, pgid = int(parts[0]), int(parts[1])
        except ValueError:
            continue
        groups.setdefault(pgid, []).append(pid)
    return groups


def _managed_candidates(app, groups):
    token = app.get("runToken")
    pgid = app.get("lastPgid") or app.get("lastPid")
    if not isinstance(token, str) or not token or not isinstance(pgid, int) or pgid <= 0:
        return set()
    return set(groups.get(pgid, []))


def managed_process_index(apps, groups=None):
    """批量校验应用的受控进程，返回 (appId -> [pid], ps, groups)。

    必须同时满足：属于记录的进程组、属于当前用户、argv 中带本次启动的
    随机 token。即使 PID/PGID 被系统复用，也不会把无关进程当成应用或停止它。
    """
    if groups is None:
        needs_groups = any(
            app.get("runToken")
            and isinstance(app.get("lastPgid") or app.get("lastPid"), int)
            for app in apps)
        groups = pgid_members_map() if needs_groups else {}
    candidates = {}
    all_pids = set()
    for app in apps:
        pids = _managed_candidates(app, groups)
        candidates[app.get("id")] = pids
        all_pids.update(pids)
    snap = ps_snapshot(all_pids, with_uid=True) if all_pids else {}
    result = {}
    for app in apps:
        token = app.get("runToken")
        marker = RUN_TOKEN_ARG_PREFIX + token if token else None
        current_user = sorted(
            pid for pid in candidates.get(app.get("id"), set())
            if snap.get(pid, {}).get("uid") == SELF_UID)
        controller_found = bool(marker and any(
            marker in snap.get(pid, {}).get("args", "") for pid in current_user))
        # 随机标记在进程组的常驻外层 shell 上；校验后整组均为受控后代。
        result[app.get("id")] = current_user if controller_found else []
    return result, snap, groups


def managed_pids(app, groups=None):
    index, _, _ = managed_process_index([app], groups)
    return index.get(app.get("id"), [])


def legacy_managed_pid(app, listeners=None, snap=None, cwds=None):
    """识别升级前已由总控台启动、但尚无 runToken 的监听进程。

    只接受配置中原本记录的 lastPid，并同时校验端口、当前用户和真实 cwd；
    不能满足全部条件时仍视为外部占用，绝不只凭端口认领进程。
    """
    if app.get("runToken"):
        return None
    pid = app.get("lastPid")
    port = app.get("port")
    expected_cwd = app.get("cwd")
    if (not isinstance(pid, int) or pid <= 0
            or not isinstance(port, int) or port <= 0
            or not isinstance(expected_cwd, str) or not expected_cwd):
        return None
    if listeners is None:
        listeners = scan_listeners()
    if (pid, port) not in listeners:
        return None
    if snap is None:
        snap = ps_snapshot({pid}, with_uid=True)
    if snap.get(pid, {}).get("uid") != SELF_UID:
        return None
    if cwds is None:
        cwds = lsof_cwds({pid})
    actual_cwd = cwds.get(pid)
    if not actual_cwd:
        return None
    try:
        if os.path.realpath(actual_cwd) != os.path.realpath(expected_cwd):
            return None
    except OSError:
        return None
    return pid


def build_apps(cfg, listeners):
    """token 校验通过或严格命中旧版身份的进程才算 running。

    额外显式返回“配置重复”与“端口被其他进程占用”，不再把任意
    监听者误当成应用本身。
    """
    port_map = {}
    for pid, port in listeners:
        port_map.setdefault(port, []).append(pid)
    apps_cfg = cfg.get("apps") or []
    managed, snap, _ = managed_process_index(apps_cfg)
    listen_by_pid = {}
    for pid, port in listeners:
        listen_by_pid.setdefault(pid, []).append(port)
    configured = {}
    for app in apps_cfg:
        if app.get("port"):
            configured.setdefault(app["port"], []).append(app)

    # 端口诊断需要展示占用者的真实身份，一次批量取详情，避免逐卡 ps。
    configured_listener_pids = {
        pid for port in configured for pid in port_map.get(port, [])}
    listener_snap = (ps_snapshot(configured_listener_pids, with_uid=True)
                     if configured_listener_pids else {})
    listener_cwds = lsof_cwds(configured_listener_pids)
    managed_owner = {}
    for owner_app in apps_cfg:
        for owner_pid in managed.get(owner_app.get("id"), []):
            managed_owner[owner_pid] = owner_app

    apps = []
    for app in apps_cfg:
        managed_live = managed.get(app["id"], [])
        legacy_pid = None if managed_live else legacy_managed_pid(
            app, listeners, listener_snap, listener_cwds)
        live = managed_live or ([legacy_pid] if legacy_pid else [])
        lp = app.get("lastPid")
        pid = lp if lp in live else (live[0] if live else None)
        port = app.get("port")
        configured_listeners = port_map.get(port, []) if port else []
        listening = bool(port and any(p in live for p in configured_listeners))
        occupied = bool(port and configured_listeners and not listening)
        owner_pid = configured_listeners[0] if occupied else None
        owner_info = listener_snap.get(owner_pid, {}) if owner_pid else {}
        owner_app = managed_owner.get(owner_pid)
        owner_cwd = listener_cwds.get(owner_pid) if owner_pid else None
        port_owner = None
        if owner_pid:
            comm = owner_info.get("comm") or ""
            port_owner = {
                "pid": owner_pid,
                "name": os.path.basename(comm) or "?",
                "cmd": owner_info.get("args") or comm,
                "cwd": owner_cwd,
                "project": project_name(owner_cwd),
                "uid": owner_info.get("uid"),
                "currentUser": owner_info.get("uid") == SELF_UID,
                "uptimeSec": owner_info.get("etime"),
                "appId": owner_app.get("id") if owner_app else None,
                "appName": owner_app.get("name") if owner_app else None,
            }
        conflicts = [other.get("name") or other.get("id")
                     for other in configured.get(port, [])
                     if other.get("id") != app.get("id")] if port else []
        actual_ports = sorted({p for member in live
                               for p in listen_by_pid.get(member, [])})
        apps.append({
            "id": app["id"], "name": app["name"], "command": app["command"],
            "cwd": app.get("cwd"), "port": port,
            "emoji": app.get("emoji"), "glyph": app.get("glyph"), "icon": app.get("icon"),
            "favicon": app.get("favicon"),
            "running": bool(live), "pid": pid,
            "uptimeSec": ((snap.get(pid) or listener_snap.get(pid) or {}).get("etime")
                          if pid else None),
            "kind": app.get("kind") or "service",
            "lastExit": app.get("lastExit"),
            "ports": actual_ports,
            "listening": listening,
            "portOccupied": occupied,
            "portOccupiedPid": configured_listeners[0] if occupied else None,
            "portOwner": port_owner,
            "portConflict": bool(conflicts),
            "portConflictApps": conflicts,
            "legacyManaged": bool(legacy_pid),
        })
    return apps


def build_state(cfg, console_port, config_health=None):
    degraded_reasons = []
    try:
        services, listeners = build_services(cfg)
    except Exception as e:
        LOG.exception("构建服务监控状态失败")
        services, listeners = [], set()
        degraded_reasons.append({"component": "services", "error": str(e)})
    try:
        watched = build_watched(cfg.get("watchedKeywords"))
    except Exception as e:
        LOG.exception("构建关注进程状态失败")
        watched = []
        degraded_reasons.append({"component": "watched", "error": str(e)})
    try:
        apps = build_apps(cfg, listeners)
    except Exception as e:
        LOG.exception("构建启动台状态失败")
        apps = []
        degraded_reasons.append({"component": "apps", "error": str(e)})
    if VERSION_LOAD_ERROR:
        degraded_reasons.append(
            {"component": "version", "error": VERSION_LOAD_ERROR})
    for issue in (config_health or {}).get("issues", []):
        degraded_reasons.append({"component": "config", "error": issue})
    return {
        "services": services,
        "watched": watched,
        "apps": apps,
        "watchedKeywords": cfg.get("watchedKeywords") or [],
        "consolePort": console_port,
        "consolePid": SELF_PID,
        "consoleCwd": BASE_DIR,
        "version": APP_VERSION,
        "schemaVersion": cfg.get("schemaVersion", CURRENT_SCHEMA_VERSION),
        "degraded": bool(degraded_reasons),
        "degradedReasons": degraded_reasons,
        "uiTheme": cfg.get("uiTheme") or "apollo",
        "themes": list_themes(),
    }


def build_health(cfg):
    """不执行 ps/lsof 的轻量健康检查。"""
    health = cfg.health_info()
    issues = list(health.get("issues") or [])
    if VERSION_LOAD_ERROR:
        issues.append("VERSION 读取失败: %s" % VERSION_LOAD_ERROR)
    for label, path in (("data", DATA_DIR), ("icons", ICONS_DIR),
                        ("logs", LOGS_DIR)):
        if not os.path.isdir(path):
            issues.append("%s 目录不存在" % label)
        elif not os.access(path, os.R_OK | os.W_OK | os.X_OK):
            issues.append("%s 目录不可读写" % label)
        else:
            try:
                mode = os.lstat(path).st_mode
                if stat.S_ISLNK(mode) or mode & 0o077:
                    issues.append("%s 目录权限不是 0700" % label)
            except OSError as e:
                issues.append("无法检查 %s 目录: %s" % (label, e))
    for label, path in (("config", CONFIG_PATH),
                        ("configBackup", CONFIG_PATH + ".bak")):
        try:
            mode = os.lstat(path).st_mode
        except FileNotFoundError:
            if label == "config":
                issues.append("主配置文件不存在")
            continue
        except OSError as e:
            issues.append("无法检查 %s: %s" % (label, e))
            continue
        if not stat.S_ISREG(mode) or mode & 0o077:
            issues.append("%s 文件权限不是 0600" % label)
    degraded = bool(issues)
    snapshot = cfg.snapshot()
    return {
        "ok": not degraded,
        "status": "degraded" if degraded else "ok",
        "version": APP_VERSION,
        "schemaVersion": snapshot.get(
            "schemaVersion", CURRENT_SCHEMA_VERSION),
        "degraded": degraded,
        "issues": issues,
        "config": health,
    }


def list_themes():
    """扫描 static/themes/*.json 主题清单（css 文件必须存在），供注册切换。"""
    themes = []
    try:
        names = sorted(os.listdir(THEMES_DIR))
    except OSError:
        return themes
    for name in names:
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(THEMES_DIR, name), "r", encoding="utf-8") as f:
                meta = json.load(f)
            theme_id = str(meta.get("id") or os.path.splitext(name)[0])
            if not theme_id or not os.path.isfile(
                    os.path.join(THEMES_DIR, theme_id + ".css")):
                continue
            themes.append({
                "id": theme_id,
                "name": str(meta.get("name") or theme_id),
                "author": str(meta.get("author") or ""),
                "desc": str(meta.get("desc") or ""),
                "colors": [str(c) for c in (meta.get("colors") or [])][:6],
            })
        except Exception:
            LOG.exception("读取主题清单失败: %s", name)
    return themes


# ---------------------------------------------------------------- 进程/应用操作

def process_uid(pid):
    """返回进程 uid；进程不存在返回 None。"""
    out = run_cmd(["ps", "-o", "uid=", "-p", str(int(pid))])
    toks = out.split()
    if not toks:
        return None
    try:
        return int(toks[0])
    except ValueError:
        return None


def kill_process(pid, force):
    """结束单个进程；只允许当前用户的进程。返回 (ok, error)。"""
    if pid == SELF_PID:
        return False, "不能结束总控台自身进程"
    uid = process_uid(pid)
    if uid is None:
        return False, "进程不存在"
    if uid != SELF_UID:
        return False, "只能结束当前用户的进程"
    sig = signal.SIGKILL if force else signal.SIGTERM
    try:
        os.kill(pid, sig)
    except ProcessLookupError:
        return False, "进程不存在"
    except PermissionError:
        return False, "没有权限结束该进程"
    except OSError as e:
        return False, "结束失败: %s" % e
    return True, None


def stop_pid_tree(pid, sig=signal.SIGTERM):
    """向受控进程组发信号；返回 (ok, error)。

    ProcessLookupError means the target completed between validation and the
    signal and is therefore an idempotent success. Permission and other OS
    failures must never be swallowed: callers use them to retain management
    identity instead of creating an orphan process.
    """
    try:
        os.killpg(int(pid), sig)
        return True, None
    except ProcessLookupError:
        return True, None
    except PermissionError:
        return False, "没有权限停止受控进程组"
    except OSError as e:
        return False, "停止受控进程组失败: %s" % e


def app_running(app, listeners=None):
    return bool(managed_pids(app) or legacy_managed_pid(app, listeners))


def app_alive_sign(app, listeners=None):
    """start/stop 的存活判断：新版 token 或严格校验通过的旧版身份。"""
    return app_running(app, listeners)


def build_launch_env(token, environ=None):
    """构建无 Terminal 启动时仍可找到常见开发工具的环境。

    Finder/LSUIElement 启动的应用通常只有系统 PATH，不会读取用户 shell 配置；
    因此显式补入 Homebrew、npm/pnpm、Volta、NVM、fnm 等常见目录。
    """
    env = dict(os.environ if environ is None else environ)
    home = os.path.expanduser("~")
    preferred = [
        os.path.join(home, ".local", "bin"),
        os.path.join(home, ".volta", "bin"),
        os.path.join(home, ".bun", "bin"),
        os.path.join(home, "Library", "pnpm"),
        os.path.join(home, ".asdf", "shims"),
        "/opt/homebrew/bin", "/opt/homebrew/sbin",
        "/usr/local/bin", "/usr/local/sbin",
    ]
    preferred.extend(sorted(
        glob.glob(os.path.join(home, ".nvm", "versions", "node", "*", "bin")),
        reverse=True))
    preferred.extend(sorted(
        glob.glob(os.path.join(home, ".fnm", "node-versions", "*", "installation", "bin")),
        reverse=True))
    preferred.extend((env.get("PATH") or "").split(os.pathsep))
    preferred.extend(("/usr/bin", "/bin", "/usr/sbin", "/sbin"))
    seen = set()
    env["PATH"] = os.pathsep.join(
        path for path in preferred if path and not (path in seen or seen.add(path)))
    env.setdefault("PNPM_HOME", os.path.join(home, "Library", "pnpm"))
    env[RUN_TOKEN_ENV] = token
    return env


def start_app(app):
    """返回 (ok, error, proc|None, pgid|None, token|None)。"""
    _ensure_private_dir(LOGS_DIR)
    log_path = os.path.join(LOGS_DIR, "%s.log" % app["id"])
    rotate_log_file(log_path)
    cwd = app.get("cwd") or os.path.expanduser("~")
    try:
        log_fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND,
                         0o600)
        os.fchmod(log_fd, 0o600)
        logf = os.fdopen(log_fd, "ab", buffering=0)
    except OSError as e:
        return False, "无法打开日志文件: %s" % e, None, None, None
    token = secrets.token_urlsafe(24)
    env = build_launch_env(token)
    marker = RUN_TOKEN_ARG_PREFIX + token
    # 外层 shell 在 argv[0] 中持有随机标记并等待内层；内层等待用户命令
    # 留下的后台作业。因此进程组既可验证，也不会因启动脚本过早退出而失去锚点。
    outer_script = '/bin/bash -c "$1"\nconsole_status=$?\nexit "$console_status"'
    inner_script = (app["command"] +
                    '\nconsole_status=$?\nwait\nexit "$console_status"')
    try:
        header = "\n===== 启动于 %s =====\n" % time.strftime("%Y-%m-%d %H:%M:%S")
        logf.write(header.encode("utf-8"))
        proc = subprocess.Popen(
            ["/bin/bash", "-c", outer_script, marker, inner_script],
            cwd=cwd, stdout=logf, stderr=subprocess.STDOUT,
            start_new_session=True, env=env)
    except Exception as e:
        logf.close()
        return False, "启动失败: %s" % e, None, None, None
    logf.close()  # 子进程已持有副本，父进程关闭避免 fd 泄漏
    return True, None, proc, proc.pid, token


def startup_failure_message(app_id, code):
    """从日志末尾提取一行可直接显示给用户的启动错误。"""
    text = read_log_tail(app_id, 30)
    for line in reversed(text.splitlines()):
        line = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", line).strip()
        if line and not line.startswith("====="):
            if len(line) > 180:
                line = line[:179] + "…"
            return "启动命令立即退出（exit %s）：%s" % (code, line)
    return "启动命令立即退出（exit %s），请查看日志" % code


def watch_app_exit(cfg, app_id, proc, token, started_at=None):
    """后台线程等子进程退出：若期间未被手动 stop/重启（lastPid 仍指向它），
    记录 lastExit（退出码、结束时间和运行耗时）。保留 lastPid 作为进程组锚点——
    脚本可能把服务放后台后退出，后续的运行判定/停止都靠 pgid 找到存活成员。"""
    started_at = time.time() if started_at is None else started_at

    def _wait():
        code = proc.wait()
        ended_at = time.time()
        duration = round(max(0.0, ended_at - started_at), 3)

        with MANUAL_STOP_LOCK:
            manually_stopped = (app_id, token) in MANUAL_STOP_TOKENS

        def op(c):
            target = find_app(c, app_id)
            if (not manually_stopped and target
                    and target.get("lastPid") == proc.pid
                    and target.get("runToken") == token):
                target["lastExit"] = {
                    "code": code,
                    "at": int(ended_at),
                    "startedAt": int(started_at * 1000),
                    "durationSec": duration,
                }
        cfg.update(op)
        rotate_log_file(os.path.join(LOGS_DIR, "%s.log" % app_id))
    thread = threading.Thread(target=_wait, daemon=True)
    thread.start()
    return thread


def persist_started_app(cfg, app_id, proc, pgid, token):
    """保存新的受控身份并启动退出监视线程。"""
    started_at = time.time()

    def op(c):
        target = find_app(c, app_id)
        if target:
            target["lastPid"] = proc.pid
            target["lastPgid"] = pgid
            target["runToken"] = token
            # 批处理任务运行时保留上一次完成结果；如果用户手动停止本次任务，
            # 卡片仍可显示之前的历史，而不是退回“未运行”。自然退出会覆盖它。
            if (target.get("kind") or "service") != "task":
                target["lastExit"] = None
            return True
        return False
    saved = cfg.update(op)
    if saved:
        watch_app_exit(cfg, app_id, proc, token, started_at)
    return saved


def clear_app_runtime(cfg, app_id, expected_token=None):
    """清除应用的受控身份；可用 token 防止并发重启互相覆盖。"""
    def op(c):
        target = find_app(c, app_id)
        if not target:
            return False
        if expected_token is not None and target.get("runToken") != expected_token:
            return False
        target["lastPid"] = None
        target["lastPgid"] = None
        target["runToken"] = None
        return True
    return cfg.update(op)


def stop_app_for_update(cfg, app, timeout=5.0):
    """为修改运行参数安全停止应用；返回 (ok, error, stopped)。"""
    if not app_alive_sign(app):
        return True, None, False
    ok, error = stop_app_and_clear(cfg, app, timeout)
    return ok, error, bool(ok)


def pick_path(what):
    """macOS 原生文件/目录选择框（osascript）。返回 (path|None, canceled)。"""
    if what == "dir":
        script = 'POSIX path of (choose folder with prompt "选择工作目录")'
    else:
        script = 'POSIX path of (choose file with prompt "选择批处理脚本")'
    try:
        r = subprocess.run(["osascript", "-e", script],
                           capture_output=True, text=True, timeout=180)
    except Exception:
        return None, False
    if r.returncode != 0:  # 用户按了取消（"User canceled."）
        return None, True
    return r.stdout.strip().rstrip("/") or None, False


# ---------------------------------------------------------------- 项目启动识别

def _read_project_text(root, name):
    """只读取项目根目录下的小型文本配置；不存在、过大或不可读均返回 None。"""
    path = os.path.join(root, name)
    try:
        if not os.path.isfile(path) or os.path.getsize(path) > MAX_DETECT_FILE_BYTES:
            return None
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read(MAX_DETECT_FILE_BYTES + 1)
    except OSError:
        return None


def _port_from_command(command):
    """从常见 CLI 参数和环境变量中提取显式端口。"""
    patterns = (
        r"(?:^|\s)--port(?:=|\s+)(\d{1,5})(?=\s|$)",
        r"(?:^|\s)-p\s+(\d{1,5})(?=\s|$)",
        r"(?:^|\s)PORT\s*=\s*(\d{1,5})(?=\s|$)",
        r"(?:localhost|127\.0\.0\.1|0\.0\.0\.0):(\d{1,5})",
        r"\bhttp\.server\s+(\d{1,5})(?=\s|$)",
    )
    for pattern in patterns:
        match = re.search(pattern, command, re.IGNORECASE)
        if match:
            port = int(match.group(1))
            if 1 <= port <= 65535:
                return port
    return None


def _package_default_port(script_name, command, dependencies):
    """根据直接依赖和脚本内容给出开发服务器的惯用端口。"""
    haystack = " ".join((script_name, command, " ".join(dependencies))).lower()
    defaults = (
        (("hexo",), 4000),
        (("gatsby",), 8000),
        (("@docusaurus/", "docusaurus"), 3000),
        (("vuepress",), 8080),
        (("docsify",), 3000),
        (("eleventy", "@11ty/eleventy"), 8080),
        (("astro",), 4321),
        (("next", "nextjs"), 3000),
        (("nuxt",), 3000),
        (("react-scripts",), 3000),
        (("vue-cli-service", "@vue/cli-service"), 8080),
        (("vite",), 4173 if script_name == "preview" else 5173),
    )
    for needles, port in defaults:
        if any(needle in haystack for needle in needles):
            return port
    return None


def detect_project(root):
    """只读分析项目根目录，返回可由启动台直接使用的启动候选。"""
    if not isinstance(root, str) or not root.strip():
        return None, "请选择项目文件夹"
    root = os.path.abspath(os.path.expanduser(root.strip()))
    if not os.path.isdir(root):
        return None, "项目文件夹不存在或不可访问"

    candidates = []
    detected_files = []

    def note_file(name, text=None):
        path = os.path.join(root, name)
        exists = text is not None or os.path.isfile(path)
        if exists and name not in detected_files:
            detected_files.append(name)
        return exists

    def add(command, label, source, port=None, priority=50, detail=None,
            kind="service"):
        if not command or any(item["command"] == command for item in candidates):
            return
        if port is not None and not (isinstance(port, int) and 1 <= port <= 65535):
            port = None
        candidates.append({
            "command": command,
            "label": label,
            "source": source,
            "port": port,
            "kind": "task" if kind == "task" else "service",
            "detail": detail,
            "_priority": priority,
        })

    # Node / 前端 / 博客项目：优先读取 package.json 的 scripts。
    package = {}
    scripts = {}
    deps = set()
    hexo_config = os.path.isfile(os.path.join(root, "_config.yml"))
    is_hexo = hexo_config and (
        os.path.isdir(os.path.join(root, "source")) or
        os.path.isdir(os.path.join(root, "scaffolds")) or
        os.path.isdir(os.path.join(root, "themes")))
    package_text = _read_project_text(root, "package.json")
    if package_text is not None:
        note_file("package.json", package_text)
        try:
            package = json.loads(package_text)
        except (TypeError, ValueError):
            package = {}
        scripts = package.get("scripts") if isinstance(package, dict) else {}
        if not isinstance(scripts, dict):
            scripts = {}
        for key in ("dependencies", "devDependencies", "peerDependencies"):
            values = package.get(key) if isinstance(package, dict) else None
            if isinstance(values, dict):
                deps.update(str(name).lower() for name in values)
        is_hexo = (is_hexo or "hexo" in deps or
                   (isinstance(package, dict) and isinstance(package.get("hexo"), dict)))

        if os.path.isfile(os.path.join(root, "pnpm-lock.yaml")):
            runner = "pnpm run"
            note_file("pnpm-lock.yaml")
        elif (os.path.isfile(os.path.join(root, "bun.lock")) or
              os.path.isfile(os.path.join(root, "bun.lockb"))):
            runner = "bun run"
            note_file("bun.lock" if os.path.isfile(os.path.join(root, "bun.lock")) else "bun.lockb")
        elif os.path.isfile(os.path.join(root, "yarn.lock")):
            runner = "yarn"
            note_file("yarn.lock")
        else:
            runner = "npm run"

        labels = {
            "dev": "开发服务器", "develop": "开发服务器",
            "start": "正式启动", "serve": "本地服务", "server": "本地服务",
            "preview": "本地预览", "docs": "文档站",
            "storybook": "组件预览",
        }
        preferred = ("dev", "develop", "start", "serve", "server", "preview", "docs", "storybook")
        ordered = [name for name in preferred if name in scripts]
        service_name = re.compile(r"(?:^|[:_-])(dev|develop|start|serve|server|preview|watch|docs|storybook|web|blog)(?:$|[:_-])", re.I)
        ordered.extend(name for name in scripts if name not in ordered and service_name.search(str(name)))
        for index, name in enumerate(ordered[:8]):
            script = scripts.get(name)
            if not isinstance(script, str):
                continue
            if is_hexo and str(name).lower() == "server" and re.search(
                    r"\bhexo\s+(?:s|server)\b", script, re.I):
                continue  # 下方提供更短、更通用的 hexo s，不重复同一操作
            command = "%s %s" % (runner, shlex.quote(str(name)))
            port = _port_from_command(script)
            if port is None:
                port = _package_default_port(str(name).lower(), script, deps)
            add(command, labels.get(str(name).lower(), "项目脚本：%s" % name),
                "package.json · scripts.%s" % name, port,
                10 + index, "由项目自己的脚本定义")

    # Hexo 即使没有 scripts 也有稳定 CLI：服务与清缓存分别作为服务/任务。
    if is_hexo:
        if hexo_config:
            note_file("_config.yml")
        add("hexo s", "Hexo 本地服务", "Hexo 项目结构", 4000, 8,
            "等同于 hexo server")
        add("hexo cl", "Hexo 清除缓存", "Hexo 项目结构", None, 9,
            "清除缓存和已生成文件，不启动服务", kind="task")

    # 常见博客与静态站点生成器。
    hugo_config = next((name for name in ("hugo.toml", "hugo.yaml", "hugo.yml")
                        if os.path.isfile(os.path.join(root, name))), None)
    if hugo_config or (os.path.isdir(os.path.join(root, "content")) and
                       os.path.isdir(os.path.join(root, "layouts")) and
                       os.path.isfile(os.path.join(root, "config.toml"))):
        source = hugo_config or "config.toml"
        note_file(source)
        add("hugo server -D", "Hugo 本地预览", source, 1313, 18,
            "包含草稿内容")

    gemfile = _read_project_text(root, "Gemfile")
    if gemfile is not None:
        note_file("Gemfile", gemfile)
        if "jekyll" in gemfile.lower():
            add("bundle exec jekyll serve", "Jekyll 本地预览", "Gemfile", 4000, 19)

    # Python Web 项目。
    pyproject = _read_project_text(root, "pyproject.toml")
    requirements = _read_project_text(root, "requirements.txt")
    if pyproject is not None:
        note_file("pyproject.toml", pyproject)
    if requirements is not None:
        note_file("requirements.txt", requirements)
    py_deps = "\n".join(text for text in (pyproject, requirements) if text).lower()
    python_runner = "uv run" if os.path.isfile(os.path.join(root, "uv.lock")) else "python3 -m"
    if os.path.isfile(os.path.join(root, "uv.lock")):
        note_file("uv.lock")
    if os.path.isfile(os.path.join(root, "manage.py")):
        note_file("manage.py")
        prefix = "uv run python" if python_runner == "uv run" else "python3"
        add(prefix + " manage.py runserver", "Django 开发服务器", "manage.py", 8000, 20)
    else:
        for module_file in ("app.py", "main.py", "server.py"):
            module_text = _read_project_text(root, module_file)
            if module_text is None:
                continue
            module = os.path.splitext(module_file)[0]
            imports_streamlit = re.search(
                r"(?m)^\s*(?:import\s+streamlit\b|from\s+streamlit\b)", module_text)
            imports_fastapi = re.search(
                r"(?m)^\s*(?:import\s+fastapi\b|from\s+fastapi\b)", module_text)
            imports_flask = re.search(
                r"(?m)^\s*(?:import\s+flask\b|from\s+flask\b)", module_text)
            if "streamlit" in py_deps or imports_streamlit:
                note_file(module_file, module_text)
                prefix = "uv run" if python_runner == "uv run" else "python3 -m"
                add(prefix + " streamlit run " + module_file,
                    "Streamlit 应用", module_file, 8501, 22)
                break
            if "fastapi" in py_deps or imports_fastapi:
                note_file(module_file, module_text)
                prefix = "uv run" if python_runner == "uv run" else "python3 -m"
                add(prefix + " uvicorn %s:app --reload" % module,
                    "FastAPI 开发服务器", module_file, 8000, 23)
                break
            if "flask" in py_deps or imports_flask:
                note_file(module_file, module_text)
                prefix = "uv run" if python_runner == "uv run" else "python3 -m"
                add(prefix + " flask --app %s run --debug" % module,
                    "Flask 开发服务器", module_file, 5000, 24)
                break

    # Docker Compose、Go、Rust 和已有的常用启动脚本。
    compose_name = next((name for name in ("compose.yaml", "compose.yml", "docker-compose.yaml", "docker-compose.yml")
                         if os.path.isfile(os.path.join(root, name))), None)
    if compose_name:
        compose_text = _read_project_text(root, compose_name)
        note_file(compose_name, compose_text)
        port = None
        if compose_text:
            match = re.search(r"[\"']?(\d{2,5})\s*:\s*\d{2,5}[\"']?", compose_text)
            if match and 1 <= int(match.group(1)) <= 65535:
                port = int(match.group(1))
        add("docker compose up", "Docker Compose", compose_name, port, 55,
            "以前台方式运行，停止按钮可正常关闭")
    if os.path.isfile(os.path.join(root, "go.mod")):
        note_file("go.mod")
        add("go run .", "Go 项目", "go.mod", None, 60)
    if os.path.isfile(os.path.join(root, "Cargo.toml")):
        note_file("Cargo.toml")
        add("cargo run", "Rust 项目", "Cargo.toml", None, 61)

    for script_name in ("start.command", "dev.command", "run.command", "start.sh", "dev.sh", "run.sh"):
        if os.path.isfile(os.path.join(root, script_name)):
            note_file(script_name)
            add("bash %s" % shlex.quote("./" + script_name),
                "现有启动脚本", script_name, None, 70,
                "也可以继续使用“选择脚本”手动指定")
            break

    # 纯静态站点最后兜底，避免把 Vite/Next 等项目误当成普通文件目录。
    if not candidates and os.path.isfile(os.path.join(root, "index.html")):
        note_file("index.html")
        add("python3 -m http.server 8000", "静态网站预览", "index.html", 8000, 90)

    candidates.sort(key=lambda item: item.pop("_priority"))
    return {
        "ok": True,
        "cwd": root,
        "name": os.path.basename(root) or root,
        "files": detected_files,
        "candidates": candidates[:8],
    }, None


def _current_user_group_members(pgid):
    """Return live current-user members of a previously verified group.

    Once SIGTERM is sent the token-bearing controller may exit before a child
    that ignores SIGTERM.  Requiring the marker again would incorrectly report
    success, so the wait phase follows the already-verified PGID until empty.
    """
    members = pgid_members_map().get(pgid, [])
    if not members:
        return []
    snap = ps_snapshot(members, with_uid=True)
    return sorted(pid for pid in members
                  if snap.get(pid, {}).get("uid") == SELF_UID)


def resolve_app_stop_target(app, listeners=None):
    """Resolve and validate a stop target before any signal is sent."""
    current = managed_pids(app)
    if current:
        pgid = app.get("lastPgid") or app.get("lastPid")
        if isinstance(pgid, int) and pgid > 0:
            return {"kind": "group", "id": pgid, "members": list(current)}, None
        return None, "受控进程组信息无效"
    legacy_pid = legacy_managed_pid(app, listeners)
    if legacy_pid:
        return {"kind": "pid", "id": legacy_pid, "members": [legacy_pid]}, None
    return None, "无法确认受控进程，未执行停止"


def signal_app_stop(target, sig=signal.SIGTERM):
    """Signal a target returned by resolve_app_stop_target."""
    ident = target["id"]
    if target["kind"] == "group":
        return stop_pid_tree(ident, sig)
    try:
        os.kill(ident, sig)
        return True, None
    except ProcessLookupError:
        return True, None
    except PermissionError:
        return False, "没有权限停止受控进程"
    except OSError as e:
        return False, "停止受控进程失败: %s" % e


def stop_target_alive(target):
    if target["kind"] == "group":
        try:
            os.killpg(target["id"], 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OSError:
            return True
    try:
        os.kill(target["id"], 0)
        return process_uid(target["id"]) == SELF_UID
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return True


def stop_app_and_wait(app, timeout=APP_STOP_TIMEOUT_SEC, listeners=None):
    """Signal a verified app and wait until the exact target is gone.

    Returns (ok, error).  A timeout is deliberately not escalated to SIGKILL;
    the caller keeps the runtime token so the user can retry or choose a force
    action without losing control of a still-live process.
    """
    target, error = resolve_app_stop_target(app, listeners)
    if target is None:
        return False, error
    ok, error = signal_app_stop(target)
    if not ok:
        return False, error
    deadline = time.monotonic() + max(0.0, timeout)
    while stop_target_alive(target):
        if time.monotonic() >= deadline:
            remaining = (target["members"] if target["kind"] == "pid"
                         else _current_user_group_members(target["id"]))
            suffix = "（PID %s）" % "、".join(str(p) for p in remaining) if remaining else ""
            return False, "应用未在 %.1f 秒内退出%s，仍保留管理状态" % (timeout, suffix)
        time.sleep(0.05)
    return True, None


def stop_app_and_clear(cfg, app, timeout=APP_STOP_TIMEOUT_SEC, listeners=None):
    """Manual stop transaction: wait first, clear persisted identity last."""
    marker = (app.get("id"), app.get("runToken"))
    with MANUAL_STOP_LOCK:
        MANUAL_STOP_TOKENS.add(marker)
    try:
        ok, error = stop_app_and_wait(app, timeout, listeners)
        if not ok:
            return False, error
        if not clear_app_runtime(cfg, app["id"], app.get("runToken")):
            return False, "进程已停止，但应用状态已变化，请刷新后重试"
        return True, None
    finally:
        with MANUAL_STOP_LOCK:
            MANUAL_STOP_TOKENS.discard(marker)


def stop_app(app, listeners=None):
    """兼容旧调用：只发送停止信号并准确报告失败，不清除配置状态。"""
    target, _ = resolve_app_stop_target(app, listeners)
    if target is None:
        return False
    ok, _ = signal_app_stop(target)
    return ok


# ---------------------------------------------------------------- 日志

def rotate_log_file(path, max_bytes=MAX_LOG_BYTES, backups=LOG_BACKUPS):
    """超限后 copy-truncate，保持子进程已打开的文件描述符继续可写。"""
    with LOG_LOCK:
        try:
            if os.path.getsize(path) <= max_bytes:
                return False
        except OSError:
            return False
        try:
            for index in range(backups, 1, -1):
                older = "%s.%d" % (path, index - 1)
                newer = "%s.%d" % (path, index)
                if os.path.exists(older):
                    os.replace(older, newer)
            shutil.copyfile(path, path + ".1")
            os.chmod(path + ".1", 0o600)
            with open(path, "r+b") as f:
                f.truncate(0)
            os.chmod(path, 0o600)
            return True
        except OSError:
            LOG.exception("轮转日志失败: %s", path)
            return False


def _tail_file_lines(path, count, block_size=65536):
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            pos = f.tell()
            chunks = []
            newlines = 0
            while pos > 0 and newlines <= count:
                size = min(block_size, pos)
                pos -= size
                f.seek(pos)
                chunk = f.read(size)
                chunks.append(chunk)
                newlines += chunk.count(b"\n")
        data = b"".join(reversed(chunks))
        return data.decode("utf-8", errors="replace").splitlines()[-count:]
    except OSError:
        return []


def read_log_tail(app_id, count):
    """从当前日志和轮转备份中高效读取最后 count 行。"""
    path = os.path.join(LOGS_DIR, "%s.log" % app_id)
    rotate_log_file(path)
    collected = []
    with LOG_LOCK:
        for candidate in [path] + ["%s.%d" % (path, i)
                                   for i in range(1, LOG_BACKUPS + 1)]:
            remaining = count - len(collected)
            if remaining <= 0:
                break
            lines = _tail_file_lines(candidate, remaining)
            collected = lines + collected
    return "\n".join(collected[-count:])


def start_log_maintenance():
    def _maintain():
        while True:
            try:
                for name in os.listdir(LOGS_DIR):
                    if name.endswith(".log"):
                        rotate_log_file(os.path.join(LOGS_DIR, name))
            except OSError:
                LOG.exception("日志维护失败")
            time.sleep(LOG_MAINTENANCE_SEC)
    threading.Thread(target=_maintain, daemon=True).start()


def sniff_image(data):
    """magic bytes 校验 → "png" / "jpg" / "webp" / None。"""
    if len(data) >= 8 and data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if len(data) >= 3 and data[:3] == b"\xff\xd8\xff":
        return "jpg"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


# ---------------------------------------------------------------- 站点图标抓取

ICON_LINK_RE = re.compile(
    r"<link[^>]+rel=[\"'][^\"']*icon[^\"']*[\"'][^>]*>", re.I)
HREF_RE = re.compile(r"href=[\"']([^\"']+)[\"']", re.I)


def is_loopback_service_url(url, port):
    """仅允许抓取指定端口的明文 loopback URL，避免 favicon SSRF。"""
    try:
        parsed = urllib.parse.urlsplit(url)
        return (parsed.scheme == "http"
                and (parsed.hostname or "").lower() in (
                    "127.0.0.1", "localhost", "::1")
                and parsed.port == port
                and not parsed.username and not parsed.password)
    except (TypeError, ValueError, UnicodeError):
        return False


class LoopbackRedirectHandler(urllib.request.HTTPRedirectHandler):
    """只跟随仍停留在同一 loopback 端口的重定向。"""

    def __init__(self, port):
        super().__init__()
        self.port = port

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not is_loopback_service_url(newurl, self.port):
            return None
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def http_get(url, port, timeout=3, limit=262144):
    """GET → (bytes, content-type) | (None, None)。仅抓同一 loopback 端口。"""
    if not is_loopback_service_url(url, port):
        return None, None
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "Console/1.0", "Accept": "*/*"})
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), LoopbackRedirectHandler(port))
        with opener.open(req, timeout=timeout) as r:
            return r.read(limit), (r.headers.get("Content-Type") or "")
    except Exception:
        return None, None


def sniff_icon_bytes(data, ctype=""):
    """→ "png" / "jpg" / "webp" / "ico" / None。拒绝主动 SVG 内容。"""
    if len(data) >= 4 and data[:4] == b"\x00\x00\x01\x00":
        return "ico"
    ext = sniff_image(data)
    if ext:
        return ext
    return None


def fetch_favicon(port):
    """抓 http://127.0.0.1:{port} 的站点图标 → (bytes, ext) | (None, None)。
    先解析首页 <link rel=...icon...>（含 apple-touch-icon），兜底 /favicon.ico。"""
    base = "http://127.0.0.1:%d" % port
    candidates = []
    html, _ = http_get(base + "/", port)
    if html:
        text = html.decode("utf-8", errors="replace")
        for m in ICON_LINK_RE.finditer(text):
            hm = HREF_RE.search(m.group(0))
            if hm:
                url = urllib.parse.urljoin(base + "/", hm.group(1))
                if is_loopback_service_url(url, port):
                    candidates.append(url)
    candidates.append(base + "/favicon.ico")
    for url in candidates[:4]:
        data, ctype = http_get(url, port, limit=1024 * 1024)
        if data:
            ext = sniff_icon_bytes(data, ctype)
            if ext:
                return data, ext
    return None, None


def find_app(cfg, app_id):
    for app in cfg.get("apps") or []:
        if app.get("id") == app_id:
            return app
    return None


def find_port_conflicts(cfg, port, exclude_id=None):
    if not port:
        return []
    return [app for app in cfg.get("apps") or []
            if app.get("port") == port and app.get("id") != exclude_id]


def diagnose_app(cfg, app):
    """规则诊断：退出码 + 日志模式 + 文件系统检查 → 可执行的修复建议列表。

    覆盖常见失败：依赖未装、命令/脚本不存在、运行时缺失、npm 脚本名错误、
    端口占用、权限不足、Python 包缺失、配置端口重复。
    """
    issues = []

    def add(kind, title, detail, fix):
        if not any(i["kind"] == kind for i in issues):
            issues.append({"kind": kind, "title": title,
                           "detail": detail, "fix": fix})

    app_id = app.get("id") or ""
    cwd = app.get("cwd") or ""
    last_exit = app.get("lastExit") or {}
    code = last_exit.get("code")
    port = app.get("port")
    log_tail = read_log_tail(app_id, 150) if app_id else ""
    log_lower = log_tail.lower()

    # ---- 配置层检查（不依赖日志） ----
    if cwd and not os.path.isdir(cwd):
        add("cwd-missing", "工作目录不存在",
            "配置的目录不存在：%s" % cwd,
            "确认目录是否被移动/删除、外接磁盘是否已挂载，然后在编辑里重新选择工作区。")

    pkg_json = os.path.join(cwd, "package.json") if cwd else ""
    has_pkg = bool(cwd) and os.path.isfile(pkg_json)
    has_node_modules = bool(cwd) and os.path.isdir(os.path.join(cwd, "node_modules"))
    if has_pkg and not has_node_modules:
        mgr = ("yarn" if os.path.isfile(os.path.join(cwd, "yarn.lock"))
               else "pnpm" if os.path.isfile(os.path.join(cwd, "pnpm-lock.yaml"))
               else "npm")
        add("deps-missing", "依赖未安装（node_modules 缺失）",
            "目录里有 package.json，但没有 node_modules。",
            "终端执行：cd \"%s\" && %s install，装完再启动。" % (cwd, mgr))

    # 配置端口与其他应用重复
    conflicts = find_port_conflicts(cfg, port, exclude_id=app_id)
    if port and conflicts:
        add("port-dup", "端口与其他应用重复",
            ":%s 还被「%s」配置。" % (port, "、".join(a.get("name") or a.get("id") for a in conflicts)),
            "给其中一个应用改成不同的端口。")

    # ---- 日志模式匹配 ----
    m = re.search(r"cannot find module '([^']+)'", log_lower)
    if m:
        add("deps-missing", "找不到模块 %s" % m.group(1),
            "日志报 Cannot find module '%s'，通常是依赖没装或装坏了。" % m.group(1),
            "终端执行：cd \"%s\" && npm install（仍报错再 rm -rf node_modules 后重装）。" % (cwd or "<项目目录>"))

    m = re.search(r"(?:env: )?(\S+): (?:no such file or directory|command not found)", log_lower)
    if m and "cannot find module" not in log_lower:
        add("runtime-missing", "找不到运行时：%s" % m.group(1),
            "系统里找不到 %s 这个命令。" % m.group(1),
            "确认该运行时已安装（如 node / python3 / pnpm）；总控台启动时会补常见 PATH，但程序本身需要存在。")

    if "missing script" in log_lower and has_pkg:
        script_names = []
        try:
            with open(pkg_json, "r", encoding="utf-8") as f:
                script_names = list((json.load(f).get("scripts") or {}).keys())
        except Exception:
            pass
        hint = ("package.json 里可用的脚本：%s。" % "、".join(script_names)
                if script_names else "package.json 里没有 scripts。")
        add("npm-script", "npm 脚本名写错了",
            "日志报 missing script。%s" % hint,
            "把启动命令改成上面列出的脚本名，例如 npm run %s。" % (script_names[0] if script_names else "dev"))

    if "eaddrinuse" in log_lower or "address already in use" in log_lower:
        add("port-busy", "端口被占用",
            "日志报地址已占用%s。" % ("（:%s）" % port if port else ""),
            "点卡片上的端口数字看是谁占用的，停掉它或给本应用换个端口。")

    if "eacces" in log_lower or "permission denied" in log_lower:
        add("perm", "权限不足",
            "日志报权限不足（EACCES / permission denied）。",
            "检查文件/目录权限；脚本需要可执行权限：chmod +x <脚本>。不要简单用 sudo 运行。")

    m = re.search(r"modulenotfounderror: no module named '([^']+)'", log_lower)
    if m:
        add("pip-missing", "缺少 Python 包：%s" % m.group(1),
            "日志报 ModuleNotFoundError: No module named '%s'。" % m.group(1),
            "建议在项目目录建虚拟环境再装：python3 -m venv .venv && .venv/bin/pip install %s" % m.group(1))

    if re.search(r"no such file or directory", log_lower) and not issues:
        add("file-missing", "命令里的文件/脚本不存在",
            "日志报 No such file or directory，命令里引用的路径可能写错了。",
            "检查启动命令和工作目录里的相对路径是否正确。")

    # ---- 退出码兜底 ----
    if not issues:
        if code == 126:
            add("not-exec", "命令没有执行权限（exit 126）",
                "退出码 126 表示文件不可执行。",
                "给脚本加执行权限：chmod +x <脚本>，或用 bash <脚本> 启动。")
        elif code == 127:
            add("not-found", "命令不存在（exit 127）",
                "退出码 127 表示 shell 找不到这个命令。",
                "确认命令已安装且在 PATH 里；总控台会补常见路径，但程序本身要存在。")
        elif isinstance(code, int) and code == 0:
            add("quick-exit", "命令立即正常退出（exit 0）",
                "进程启动后马上正常结束——长期服务命令不应立刻退出。",
                "确认写的是常驻命令（如 hexo s / npm run dev），而不是一次就完成的命令。")
        elif isinstance(code, int) and code < 0:
            add("signaled", "进程被信号终止（signal %d）" % -code,
                "进程不是自然退出，是被系统信号杀掉的。",
                "常见于内存不足被系统回收或外部 kill；查看系统日志确认原因。")

    # ---- 汇总 ----
    if issues:
        summary = "发现 %d 个可能原因，按「修复建议」处理后再启动。" % len(issues)
    elif not log_tail.strip():
        summary = "暂无日志可供诊断；先启动一次让日志产生，再看完整日志定位。"
    elif code is None:
        summary = "该应用还没有退出记录；当前日志未见明显异常。"
    else:
        summary = "日志里没有命中常见错误模式，建议打开完整日志人工排查。"
    return {"ok": True, "issues": issues, "summary": summary}


def validate_port(value):
    """→ (port|None, error|None)。接受 null / 整数 / 数字字符串，范围 1-65535。"""
    if value is None or value == "":
        return None, None
    if isinstance(value, bool):
        return None, "port 必须是 1-65535 的整数"
    if isinstance(value, int):
        port = value
    elif isinstance(value, str) and value.strip().isdigit():
        port = int(value.strip())
    else:
        return None, "port 必须是 1-65535 的整数"
    if not (1 <= port <= 65535):
        return None, "port 必须在 1-65535 之间"
    return port, None


def validate_app_fields(data, partial):
    """校验/规范化应用字段。partial=True 时仅校验出现的字段。
    返回 (fields, error)：fields 为规范化后的字段子集。"""
    fields = {}
    for key in ("name", "command"):
        if key in data:
            v = data[key]
            if not isinstance(v, str) or not v.strip():
                return None, "字段 %s 必须是非空字符串" % key
            fields[key] = v.strip()
        elif not partial:
            return None, "缺少字段 %s" % key
    if "cwd" in data:
        v = data["cwd"]
        if v is not None and not isinstance(v, str):
            return None, "cwd 必须是字符串或 null"
        fields["cwd"] = (v or "").strip() or None if isinstance(v, str) else None
    elif not partial:
        fields["cwd"] = None
    if "port" in data:
        port, err = validate_port(data["port"])
        if err:
            return None, err
        fields["port"] = port
    elif not partial:
        fields["port"] = None
    if "emoji" in data:
        v = data["emoji"]
        if v is not None and not isinstance(v, str):
            return None, "emoji 必须是字符串或 null"
        fields["emoji"] = (v or None)
    elif not partial:
        fields["emoji"] = None
    if "glyph" in data:
        v = data["glyph"]
        if v is not None and (not isinstance(v, str) or len(v) > 40):
            return None, "glyph 必须是字符串或 null"
        fields["glyph"] = (v or None)
    elif not partial:
        fields["glyph"] = None
    if "kind" in data:
        if data["kind"] not in ("service", "task"):
            return None, "kind 必须是 service/task"
        fields["kind"] = data["kind"]
    elif not partial:
        fields["kind"] = "service"
    if fields.get("kind") == "task":
        fields["port"] = None  # 批处理任务无端口语义
    return fields, None


# ---------------------------------------------------------------- HTTP 处理

def serialized_app_operation(fn):
    """Reject overlapping mutations for one app instead of racing/queueing."""
    @functools.wraps(fn)
    def wrapped(self, app_id, *args, **kwargs):
        lock = self.server.try_app_operation(app_id)
        if lock is None:
            self.send_err(409, "该应用正在执行其他操作，请稍后重试")
            return None
        try:
            return fn(self, app_id, *args, **kwargs)
        finally:
            lock.release()
    return wrapped


class ConsoleServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, addr, handler_cls, cfg, port):
        super().__init__(addr, handler_cls)
        self.cfg = cfg
        self.console_port = self.server_address[1]
        self.control_token = secrets.token_urlsafe(32)
        self._app_locks = {}
        self._app_locks_guard = threading.Lock()
        self._console_action_guard = threading.Lock()
        self._console_action = None
        self._console_helper_pid = None

    def try_app_operation(self, app_id):
        with self._app_locks_guard:
            lock = self._app_locks.setdefault(app_id, threading.Lock())
        return lock if lock.acquire(blocking=False) else None

    def reserve_console_action(self, action):
        with self._console_action_guard:
            if self._console_action is not None:
                return False, self._console_action, self._console_helper_pid
            self._console_action = action
            return True, action, None

    def set_console_helper_pid(self, pid):
        with self._console_action_guard:
            self._console_helper_pid = pid

    def release_console_action(self, action):
        with self._console_action_guard:
            if self._console_action == action:
                self._console_action = None
                self._console_helper_pid = None


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "Console/%s" % APP_VERSION

    # ---------- 基础工具 ----------

    def log_message(self, fmt, *args):
        try:
            if self.path.startswith("/api/state"):
                return  # 2s 轮询不刷日志
        except Exception:
            pass
        sys.stderr.write("%s - %s\n" % (self.client_address[0], fmt % args))

    def _parsed_request_host(self):
        """Return (hostname, port) only for the exact local console origin."""
        raw = (self.headers.get("Host") or "").strip()
        if not raw or any(ch in raw for ch in "\r\n,@/"):
            return None
        try:
            parsed = urllib.parse.urlsplit("http://" + raw)
            hostname = (parsed.hostname or "").lower()
            port = parsed.port
        except (ValueError, UnicodeError):
            return None
        if hostname not in ("127.0.0.1", "localhost", "::1"):
            return None
        if port != self.server.console_port:
            return None
        return hostname, port

    def _request_host_allowed(self):
        if self._parsed_request_host() is None:
            return False
        try:
            return self.client_address[0] in ("127.0.0.1", "::1")
        except (AttributeError, IndexError):
            return False

    def _same_origin(self, origin, host):
        try:
            parsed = urllib.parse.urlsplit(origin)
            port = parsed.port or (80 if parsed.scheme == "http" else 443)
            return (parsed.scheme == "http"
                    and (parsed.hostname or "").lower() == host[0]
                    and port == host[1]
                    and not parsed.username and not parsed.password
                    and not parsed.path and not parsed.query and not parsed.fragment)
        except (ValueError, UnicodeError):
            return False

    def _has_control_cookie(self):
        try:
            cookie = SimpleCookie()
            cookie.load(self.headers.get("Cookie") or "")
            morsel = cookie.get("console_session")
            return bool(morsel and secrets.compare_digest(
                morsel.value, self.server.control_token))
        except (KeyError, TypeError, ValueError):
            return False

    def _deny_request(self, status, message):
        # Do not consume attacker-controlled bodies. Closing after the bounded
        # JSON error prevents keep-alive request smuggling via leftover bytes.
        self.close_connection = True
        self.send_err(status, message)
        return False

    def authorize_request(self, mutating=False, content_kind=None):
        """Enforce the loopback browser trust boundary.

        Browser writes require exact same-origin metadata plus the HttpOnly
        session cookie issued by this process. Headerless local CLI clients stay
        compatible, but JSON/image Content-Type rules keep those paths
        unavailable to simple cross-site HTML forms.
        """
        host = self._parsed_request_host()
        if host is None or not self._request_host_allowed():
            return self._deny_request(421, "请求 Host 不是当前本地控制台")
        if not mutating:
            return True

        site = (self.headers.get("Sec-Fetch-Site") or "").strip().lower()
        origin = (self.headers.get("Origin") or "").strip()
        if site and site not in ("same-origin", "none"):
            return self._deny_request(403, "拒绝跨站控制请求")
        if origin and not self._same_origin(origin, host):
            return self._deny_request(403, "请求 Origin 不是当前控制台")
        if (site or origin) and not self._has_control_cookie():
            return self._deny_request(403, "控制会话已失效，请刷新页面")

        if self.headers.get("Transfer-Encoding"):
            return self._deny_request(400, "不支持 Transfer-Encoding 请求体")

        media_type = (self.headers.get("Content-Type") or "").split(";", 1)[0]
        media_type = media_type.strip().lower()
        if content_kind == "json" and media_type != "application/json":
            return self._deny_request(415, "接口仅接受 application/json")
        if content_kind == "image" and media_type not in (
                "image/png", "image/jpeg", "image/webp",
                "application/octet-stream"):
            return self._deny_request(415, "图标接口仅接受 PNG/JPEG/WebP 原始数据")
        if content_kind:
            lengths = self.headers.get_all("Content-Length") or []
            if len(lengths) != 1:
                return self._deny_request(400, "请求必须包含唯一的 Content-Length")
            try:
                length = int(lengths[0])
            except ValueError:
                return self._deny_request(400, "非法的 Content-Length")
            limit = MAX_ICON_BYTES if content_kind == "image" else MAX_JSON_BYTES
            if length < 0 or length > limit:
                return self._deny_request(413, "请求体过大")
        return True

    def _send(self, body, status=200, ctype="text/plain; charset=utf-8"):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("Cross-Origin-Opener-Policy", "same-origin")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; base-uri 'none'; frame-ancestors 'none'; "
            "form-action 'self'; connect-src 'self'; img-src 'self' data: blob:; "
            "font-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self'")
        if self._request_host_allowed():
            self.send_header(
                "Set-Cookie",
                "console_session=%s; Path=/; HttpOnly; SameSite=Strict" %
                self.server.control_token)
        self.end_headers()
        if body:
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

    def send_json(self, obj, status=200):
        self._send(json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                   status, "application/json; charset=utf-8")

    def send_err(self, status, msg):
        self.send_json({"ok": False, "error": msg}, status)

    def discard_body(self):
        """读掉并丢弃请求体。keep-alive 连接复用前必须清空，
        否则残留字节会污染同一连接上的下一个请求（method 解析错乱 → 501）。"""
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length > 0:
            try:
                self.rfile.read(length)
            except OSError:
                pass

    def read_json_body(self):
        """→ (data|None, error|None)。非法 JSON / 非对象 / 超限都返回 error。"""
        media_type = (self.headers.get("Content-Type") or "").split(";", 1)[0]
        if media_type.strip().lower() != "application/json":
            return None, "Content-Type 必须是 application/json"
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return None, "非法的 Content-Length"
        if length < 0 or length > MAX_JSON_BYTES:
            return None, "请求体过大"
        raw = self.rfile.read(length) if length else b""
        try:
            data = json.loads(raw.decode("utf-8"))
        except Exception:
            return None, "请求体不是合法 JSON"
        if not isinstance(data, dict):
            return None, "请求体必须是 JSON 对象"
        return data, None

    def _get_app_or_404(self, app_id):
        cfg = self.server.cfg.snapshot()
        app = find_app(cfg, app_id)
        if app is None:
            self.send_err(404, "应用不存在")
            return None, None
        return cfg, app

    # ---------- GET ----------

    def do_GET(self):
        try:
            if not self.authorize_request():
                return
            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path
            if path == "/favicon.ico":
                self._send(b"", 204)
                return
            if path == "/api/health":
                self.send_json(build_health(self.server.cfg))
                return
            if path == "/api/state":
                state = build_state(self.server.cfg.snapshot(),
                                    self.server.console_port,
                                    self.server.cfg.health_info())
                self.send_json(state)
                return
            m = APP_ROUTE_RE.match(path)
            if m and m.group(2) == "logs":
                self.handle_logs(m.group(1), parsed.query)
                return
            if path.startswith("/api/"):
                self.send_err(404, "接口不存在")
                return
            if path.startswith("/icons/"):
                self.serve_icon(path)
                return
            self.serve_static(path)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:
            LOG.exception("GET %s 处理失败", self.path)
            try:
                self.send_err(500, "服务器错误: %s" % e)
            except Exception:
                pass

    def serve_static(self, path):
        rel = urllib.parse.unquote(path).lstrip("/") or "index.html"
        full = os.path.normpath(os.path.join(STATIC_DIR, rel))
        try:
            inside = os.path.commonpath([STATIC_DIR, full]) == STATIC_DIR
        except ValueError:
            inside = False
        if not inside or not os.path.isfile(full):
            if rel == "index.html":
                self._send(PLACEHOLDER_HTML.encode("utf-8"), 200,
                           "text/html; charset=utf-8")
            else:
                self._send(b"404 Not Found", 404)
            return
        ctype = STATIC_TYPES.get(os.path.splitext(full)[1].lower(),
                                 "application/octet-stream")
        try:
            with open(full, "rb") as f:
                data = f.read()
        except OSError:
            self._send(b"404 Not Found", 404)
            return
        self._send(data, 200, ctype)

    def serve_icon(self, path):
        name = os.path.basename(urllib.parse.unquote(path[len("/icons/"):]))
        ext = os.path.splitext(name)[1].lower()
        if ext not in ICON_EXTS:
            self._send(b"404 Not Found", 404)
            return
        full = os.path.join(ICONS_DIR, name)
        if not os.path.isfile(full):
            self._send(b"404 Not Found", 404)
            return
        ctype = STATIC_TYPES.get(ext, "application/octet-stream")
        try:
            with open(full, "rb") as f:
                data = f.read()
        except OSError:
            self._send(b"404 Not Found", 404)
            return
        self._send(data, 200, ctype)

    def handle_logs(self, app_id, query):
        _, app = self._get_app_or_404(app_id)
        if app is None:
            return
        try:
            tail = int(urllib.parse.parse_qs(query).get("tail", ["300"])[0])
        except (ValueError, IndexError):
            tail = 300
        tail = max(1, min(tail, 5000))
        self.send_json({"text": read_log_tail(app_id, tail)})

    # ---------- POST ----------

    def do_POST(self):
        try:
            path = urllib.parse.urlparse(self.path).path
            route_match = APP_ROUTE_RE.match(path)
            content_kind = ("image" if route_match and
                            route_match.group(2) == "icon" else "json")
            if not self.authorize_request(mutating=True,
                                          content_kind=content_kind):
                return
            if path == "/api/kill":
                self.handle_kill()
                return
            if path == "/api/services/flag":
                self.handle_flag()
                return
            if path == "/api/watch":
                self.handle_watch()
                return
            if path == "/api/ui/theme":
                self.handle_ui_theme()
                return
            if path == "/api/pick":
                self.handle_pick()
                return
            if path == "/api/project/detect":
                self.handle_project_detect()
                return
            if path == "/api/console/restart":
                self.discard_body()
                self.handle_console_restart()
                return
            if path == "/api/console/stop":
                self.discard_body()
                self.handle_console_stop()
                return
            if path == "/api/apps":
                self.handle_app_create()
                return
            if path == "/api/apps/reorder":
                self.handle_apps_reorder()
                return
            m = APP_ROUTE_RE.match(path)
            if m:
                app_id, action = m.group(1), m.group(2)
                if action == "start":
                    self.discard_body()
                    self.handle_app_start(app_id)
                    return
                if action == "stop":
                    self.discard_body()
                    self.handle_app_stop(app_id)
                    return
                if action == "restart":
                    self.discard_body()
                    self.handle_app_restart(app_id)
                    return
                if action == "diagnose":
                    self.discard_body()
                    self.handle_app_diagnose(app_id)
                    return
                if action == "icon":
                    self.handle_icon_upload(app_id)
                    return
                if action == "favicon":
                    self.discard_body()
                    self.handle_fetch_favicon(app_id)
                    return
            self.send_err(404, "接口不存在")
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:
            LOG.exception("POST %s 处理失败", self.path)
            try:
                self.send_err(500, "服务器错误: %s" % e)
            except Exception:
                pass

    def handle_pick(self):
        data, err = self.read_json_body()
        if err:
            self.send_err(400, err)
            return
        what = data.get("what")
        if what not in ("dir", "script"):
            self.send_err(400, "what 必须是 dir/script")
            return
        path, canceled = pick_path(what)
        if canceled:  # 用户取消不是错误，前端静默
            self.send_json({"ok": True, "canceled": True})
        elif not path:
            self.send_json({"ok": False, "error": "无法打开系统选择框"})
        else:
            self.send_json({"ok": True, "path": path})

    def handle_project_detect(self):
        data, err = self.read_json_body()
        if err:
            self.send_err(400, err)
            return
        result, err = detect_project(data.get("cwd"))
        if err:
            self.send_err(400, err)
            return
        self.send_json(result)

    def handle_app_diagnose(self, app_id):
        cfg = self.server.cfg.snapshot()
        app = find_app(cfg, app_id)
        if not app:
            self.send_err(404, "应用不存在")
            return
        self.send_json(diagnose_app(cfg, app))

    def handle_ui_theme(self):
        data, err = self.read_json_body()
        if err:
            self.send_err(400, err)
            return
        theme_id = str(data.get("theme") or "")
        known = {t["id"] for t in list_themes()}
        if theme_id not in known:
            self.send_err(400, "未知主题: %s" % theme_id)
            return
        self.server.cfg.update(lambda d: d.__setitem__("uiTheme", theme_id))
        self.send_json({"ok": True, "theme": theme_id})

    def handle_console_restart(self):
        reserved, current, helper_pid = self.server.reserve_console_action("restart")
        if not reserved:
            if current == "restart":
                self.send_json({"ok": True, "pid": SELF_PID,
                                "helperPid": helper_pid,
                                "port": self.server.console_port,
                                "alreadyScheduled": True})
            else:
                self.send_err(409, "总控台正在停止，无法重复重启")
            return
        try:
            helper_pid = schedule_console_restart(
                self.server, self.server.console_port)
        except OSError as e:
            self.server.release_console_action("restart")
            self.send_err(500, "无法启动重启程序: %s" % e)
            return
        self.server.set_console_helper_pid(helper_pid)
        self.send_json({"ok": True, "pid": SELF_PID,
                        "helperPid": helper_pid,
                        "port": self.server.console_port})

    def handle_console_stop(self):
        reserved, current, _ = self.server.reserve_console_action("stop")
        if not reserved:
            if current == "stop":
                self.send_json({"ok": True, "pid": SELF_PID,
                                "port": self.server.console_port,
                                "alreadyScheduled": True})
            else:
                self.send_err(409, "总控台正在重启，无法同时停止")
            return
        schedule_console_stop(self.server)
        self.send_json({"ok": True, "pid": SELF_PID,
                        "port": self.server.console_port})

    def handle_kill(self):
        data, err = self.read_json_body()
        if err:
            self.send_err(400, err)
            return
        pid = data.get("pid")
        if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
            self.send_err(400, "缺少字段 pid（正整数）")
            return
        ok, err = kill_process(pid, bool(data.get("force")))
        self.send_json({"ok": True} if ok else {"ok": False, "error": err})

    def handle_flag(self):
        data, err = self.read_json_body()
        if err:
            self.send_err(400, err)
            return
        key, flag, value = data.get("key"), data.get("flag"), data.get("value")
        if not isinstance(key, str) or not key:
            self.send_err(400, "缺少字段 key")
            return
        if flag not in ("hidden", "pinned", "promoted"):
            self.send_err(400, "flag 必须是 hidden/pinned/promoted")
            return
        if not isinstance(value, bool):
            self.send_err(400, "value 必须是布尔值")
            return

        def op(c):
            lst = c.setdefault(flag, [])
            if value and key not in lst:
                lst.append(key)
            elif not value and key in lst:
                lst.remove(key)

        self.server.cfg.update(op)
        self.send_json({"ok": True})

    def handle_watch(self):
        data, err = self.read_json_body()
        if err:
            self.send_err(400, err)
            return
        keyword, action = data.get("keyword"), data.get("action")
        if not isinstance(keyword, str) or not keyword.strip():
            self.send_err(400, "缺少字段 keyword")
            return
        if action not in ("add", "remove"):
            self.send_err(400, "action 必须是 add/remove")
            return
        keyword = keyword.strip()

        def op(c):
            kws = c.setdefault("watchedKeywords", [])
            if action == "add" and keyword not in kws:
                kws.append(keyword)
            elif action == "remove":
                c["watchedKeywords"] = [k for k in kws if k != keyword]
            return list(c["watchedKeywords"])

        keywords = self.server.cfg.update(op)
        self.send_json({"ok": True, "keywords": keywords})

    def handle_app_create(self):
        data, err = self.read_json_body()
        if err:
            self.send_err(400, err)
            return
        fields, err = validate_app_fields(data, partial=False)
        if err:
            self.send_err(400, err)
            return

        def op(c):
            conflicts = find_port_conflicts(c, fields.get("port"))
            if conflicts:
                return None, [a.get("name") or a.get("id") for a in conflicts]
            new_id = secrets.token_hex(4)
            while find_app(c, new_id):
                new_id = secrets.token_hex(4)
            app = {"id": new_id, "name": fields["name"],
                   "command": fields["command"], "cwd": fields["cwd"],
                   "port": fields["port"], "emoji": fields["emoji"],
                   "glyph": fields["glyph"], "kind": fields["kind"],
                   "icon": None, "favicon": None, "lastPid": None,
                   "lastPgid": None, "runToken": None,
                   "lastExit": None, "createdAt": int(time.time())}
            c["apps"].append(app)
            return dict(app), []

        app, conflicts = self.server.cfg.update(op)
        if conflicts:
            self.send_err(409, "端口 %d 已被应用“%s”配置" %
                          (fields["port"], "、".join(conflicts)))
            return
        self.send_json(app)

    @serialized_app_operation
    def handle_fetch_favicon(self, app_id):
        """抓取应用有效端口对应站点的 favicon，存为 data/icons/fav-{id}.{ext}。
        优先级低于用户自定义 icon/glyph，仅作兜底。"""
        _, app = self._get_app_or_404(app_id)
        if app is None:
            return
        live = set(managed_pids(app))
        port = None
        listeners = scan_listeners()
        configured_port = app.get("port")
        if configured_port and any(pid in live and p == configured_port
                                   for pid, p in listeners):
            port = configured_port
        if not port:
            owned_ports = sorted({p for pid, p in listeners if pid in live})
            port = owned_ports[0] if owned_ports else None
        if not port:
            self.send_json({"ok": False, "error": "应用未运行或无可用端口"})
            return
        data, ext = fetch_favicon(port)
        if not data:
            self.send_json({"ok": False, "error": "未找到站点图标"})
            return
        fname = "fav-%s.%s" % (app_id, ext)
        try:
            _ensure_private_dir(ICONS_DIR)
            write_private_bytes(os.path.join(ICONS_DIR, fname), data)
        except OSError as e:
            self.send_json({"ok": False, "error": "图标保存失败: %s" % e})
            return
        url = "/icons/" + fname

        def op(c):
            target = find_app(c, app_id)
            if target:
                target["favicon"] = url

        self.server.cfg.update(op)
        self.send_json({"ok": True, "favicon": url})

    def handle_apps_reorder(self):
        """按收到的 id 顺序重排 apps（Python sort 稳定：未涉及的 id 相对顺序不变，
        服务/任务两区可独立排序互不干扰）。"""
        data, err = self.read_json_body()
        if err:
            self.send_err(400, err)
            return
        ids = data.get("ids")
        if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
            self.send_err(400, "ids 必须是字符串数组")
            return
        order = {i: n for n, i in enumerate(ids)}

        def op(c):
            c["apps"].sort(key=lambda a: order.get(a.get("id"), len(order)))

        self.server.cfg.update(op)
        self.send_json({"ok": True})

    @serialized_app_operation
    def handle_app_start(self, app_id):
        cfg, app = self._get_app_or_404(app_id)
        if app is None:
            return
        conflicts = find_port_conflicts(cfg, app.get("port"), app_id)
        if conflicts:
            names = "、".join(a.get("name") or a.get("id") for a in conflicts)
            self.send_json({"ok": False, "error": "端口 %d 配置重复（%s），请先编辑其中一项" %
                            (app["port"], names)}, 409)
            return
        if app_alive_sign(app):
            self.send_json({"ok": False, "error": "应用已在运行"})
            return
        port = app.get("port")
        occupied = [(pid, p) for pid, p in scan_listeners() if p == port] if port else []
        if occupied:
            self.send_json({"ok": False, "error": "端口 %d 已被 PID %d 占用" %
                            (port, occupied[0][0])}, 409)
            return
        ok, err, proc, pgid, token = start_app(app)
        if not ok:
            self.send_json({"ok": False, "error": err})
            return
        if not persist_started_app(self.server.cfg, app_id, proc, pgid, token):
            stop_pid_tree(pgid)
            self.send_json({"ok": False, "error": "应用已被删除，已取消启动"}, 409)
            return
        # 一次性任务的正常形态就是快速退出，不能沿用服务的启动探测逻辑把
        # `echo`、清缓存等成功任务误判成“启动失败”。退出线程会独立记录结果。
        if (app.get("kind") or "service") == "task":
            self.send_json({"ok": True, "pid": proc.pid})
            return
        deadline = time.monotonic() + STARTUP_PROBE_SEC
        code = proc.poll()
        while code is None and time.monotonic() < deadline:
            time.sleep(0.025)
            code = proc.poll()
        if code is not None:
            self.send_json({"ok": False,
                            "error": startup_failure_message(app_id, code)}, 422)
            return
        self.send_json({"ok": True, "pid": proc.pid})

    @serialized_app_operation
    def handle_app_stop(self, app_id):
        _, app = self._get_app_or_404(app_id)
        if app is None:
            return
        if not app_alive_sign(app):
            self.send_json({"ok": False, "error": "应用未在运行"})
            return
        ok, error = stop_app_and_clear(self.server.cfg, app)
        if not ok:
            self.send_json({"ok": False, "error": error}, 409)
            return
        self.send_json({"ok": True})

    @serialized_app_operation
    def handle_app_restart(self, app_id):
        cfg, app = self._get_app_or_404(app_id)
        if app is None:
            return
        conflicts = find_port_conflicts(cfg, app.get("port"), app_id)
        if conflicts:
            names = "、".join(a.get("name") or a.get("id") for a in conflicts)
            self.send_err(409, "端口 %d 配置重复（%s），请先修复冲突" %
                          (app["port"], names))
            return
        if not app_alive_sign(app):
            self.send_err(409, "应用未在运行")
            return

        stopped, error = stop_app_and_clear(self.server.cfg, app)
        if not stopped:
            self.send_err(409, error or "旧进程停止失败，已取消重启")
            return

        port = app.get("port")
        occupied = [(pid, p) for pid, p in scan_listeners() if p == port] if port else []
        if occupied:
            self.send_err(409, "端口 %d 已被 PID %d 占用，旧应用已停止" %
                          (port, occupied[0][0]))
            return

        latest = self.server.cfg.snapshot()
        current = find_app(latest, app_id)
        if not current:
            self.send_err(404, "应用已被删除")
            return
        ok, err, proc, pgid, new_token = start_app(current)
        if not ok:
            self.send_err(500, err)
            return
        if not persist_started_app(
                self.server.cfg, app_id, proc, pgid, new_token):
            stop_pid_tree(pgid)
            self.send_err(409, "应用已被删除，已取消重启")
            return
        self.send_json({"ok": True, "pid": proc.pid})

    @serialized_app_operation
    def handle_icon_upload(self, app_id):
        _, app = self._get_app_or_404(app_id)
        if app is None:
            return
        try:
            length = int(self.headers.get("Content-Length") or -1)
        except ValueError:
            length = -1
        if length < 0:
            self.send_err(400, "缺少 Content-Length")
            return
        if length > MAX_ICON_BYTES:
            self.send_err(400, "图标大小不能超过 5MB")
            return
        raw = self.rfile.read(length)
        kind = sniff_image(raw)
        if kind is None:
            self.send_err(400, "仅支持 PNG / JPEG / WebP 图片")
            return
        _ensure_private_dir(ICONS_DIR)
        for ext in ICON_EXTS:
            old = os.path.join(ICONS_DIR, app_id + ext)
            if ext != "." + kind and os.path.isfile(old):
                try:
                    os.remove(old)
                except OSError:
                    pass
        fname = "%s.%s" % (app_id, kind)
        try:
            write_private_bytes(os.path.join(ICONS_DIR, fname), raw)
        except OSError as e:
            self.send_err(500, "图标保存失败: %s" % e)
            return
        icon_url = "/icons/" + fname

        def op(c):
            target = find_app(c, app_id)
            if target:
                target["icon"] = icon_url

        self.server.cfg.update(op)
        self.send_json({"ok": True, "icon": icon_url})

    # ---------- PUT ----------

    def do_PUT(self):
        operation_lock = None
        try:
            if not self.authorize_request(mutating=True,
                                          content_kind="json"):
                return
            path = urllib.parse.urlparse(self.path).path
            m = APP_ROUTE_RE.match(path)
            if not (m and m.group(2) is None):
                self.send_err(404, "接口不存在")
                return
            operation_lock = self.server.try_app_operation(m.group(1))
            if operation_lock is None:
                self.send_err(409, "该应用正在执行其他操作，请稍后重试")
                return
            data, err = self.read_json_body()
            if err:
                self.send_err(400, err)
                return
            stop_before_update = data.get("stopBeforeUpdate", False)
            if not isinstance(stop_before_update, bool):
                self.send_err(400, "stopBeforeUpdate 必须是布尔值")
                return
            cfg, app = self._get_app_or_404(m.group(1))
            if app is None:
                return
            fields, err = validate_app_fields(data, partial=True)
            if err:
                self.send_err(400, err)
                return
            if not fields:
                self.send_err(400, "没有可更新的字段")
                return
            lifecycle_fields = {"command", "cwd", "port", "kind"}
            lifecycle_changed = any(
                key in fields and fields[key] != app.get(key)
                for key in lifecycle_fields)
            if "port" in fields:
                preflight_conflicts = find_port_conflicts(
                    cfg, fields["port"], m.group(1))
                if preflight_conflicts:
                    names = "、".join(
                        a.get("name") or a.get("id") for a in preflight_conflicts)
                    self.send_err(409, "端口 %d 已被应用“%s”配置" %
                                  (fields["port"], names))
                    return
            stopped_for_update = False
            if lifecycle_changed and app_alive_sign(app):
                if not stop_before_update:
                    self.send_json({
                        "ok": False,
                        "error": "应用正在运行，请先在当前编辑面板停止服务；填写内容会保留",
                        "requiresStop": True,
                    }, 409)
                    return
                ok, stop_error, stopped_for_update = stop_app_for_update(
                    self.server.cfg, app)
                if not ok:
                    self.send_err(409, stop_error)
                    return

            def op(c):
                target = find_app(c, m.group(1))
                if "port" in fields:
                    conflicts = find_port_conflicts(c, fields["port"], m.group(1))
                    if conflicts:
                        return None, [a.get("name") or a.get("id") for a in conflicts]
                target.update(fields)
                return dict(target), []

            updated, conflicts = self.server.cfg.update(op)
            if conflicts:
                prefix = "应用已停止；" if stopped_for_update else ""
                self.send_err(409, prefix + "端口 %d 已被应用“%s”配置" %
                              (fields["port"], "、".join(conflicts)))
                return
            if stopped_for_update:
                updated = dict(updated)
                updated["stoppedForUpdate"] = True
            self.send_json(updated)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:
            LOG.exception("PUT %s 处理失败", self.path)
            try:
                self.send_err(500, "服务器错误: %s" % e)
            except Exception:
                pass
        finally:
            if operation_lock is not None:
                operation_lock.release()

    # ---------- DELETE ----------

    def do_DELETE(self):
        try:
            if not self.authorize_request(mutating=True):
                return
            path = urllib.parse.urlparse(self.path).path
            m = APP_ROUTE_RE.match(path)
            if not m:
                self.send_err(404, "接口不存在")
                return
            app_id, action = m.group(1), m.group(2)
            if action is None:
                self.handle_app_delete(app_id)
                return
            if action == "icon":
                self.handle_icon_delete(app_id)
                return
            self.send_err(404, "接口不存在")
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:
            LOG.exception("DELETE %s 处理失败", self.path)
            try:
                self.send_err(500, "服务器错误: %s" % e)
            except Exception:
                pass

    def do_OPTIONS(self):
        # No CORS endpoint exists. An explicit denial is clearer than the
        # BaseHTTPRequestHandler HTML 501 response and never grants ACAO.
        self._deny_request(403, "控制台不接受跨域预检请求")

    @serialized_app_operation
    def handle_app_delete(self, app_id):
        _, app = self._get_app_or_404(app_id)
        if app is None:
            return
        if app_running(app):
            stopped, error = stop_app_and_clear(self.server.cfg, app)
            if not stopped:
                self.send_err(409, "删除已取消：%s" %
                              (error or "应用未能正常退出"))
                return

        def op(c):
            before = len(c["apps"])
            c["apps"] = [a for a in c["apps"] if a.get("id") != app_id]
            return len(c["apps"]) != before

        if not self.server.cfg.update(op):
            self.send_err(404, "应用不存在")
            return

        for ext in ICON_EXTS:
            for fname in (app_id + ext, "fav-" + app_id + ext):
                try:
                    os.remove(os.path.join(ICONS_DIR, fname))
                except OSError:
                    pass
        log_path = os.path.join(LOGS_DIR, "%s.log" % app_id)
        for candidate in [log_path] + ["%s.%d" % (log_path, i)
                                       for i in range(1, LOG_BACKUPS + 1)]:
            try:
                os.remove(candidate)
            except OSError:
                pass

        self.send_json({"ok": True})

    @serialized_app_operation
    def handle_icon_delete(self, app_id):
        _, app = self._get_app_or_404(app_id)
        if app is None:
            return
        for ext in ICON_EXTS:
            try:
                os.remove(os.path.join(ICONS_DIR, app_id + ext))
            except OSError:
                pass

        def op(c):
            target = find_app(c, app_id)
            if target:
                target["icon"] = None

        self.server.cfg.update(op)
        self.send_json({"ok": True})


# ---------------------------------------------------------------- 启动

def open_browser_later(port, delay=0.8):
    def _open():
        try:
            time.sleep(delay)
            webbrowser.open("http://%s:%d/" % (HOST, port))
        except Exception:
            pass
    threading.Thread(target=_open, daemon=True).start()


def find_console_instances():
    """查找从同一项目目录启动的总控台，用于双击启动器去重。"""
    snap = ps_snapshot(None, with_uid=True)
    candidates = []
    for pid, info in snap.items():
        args = info.get("args") or ""
        if (pid == SELF_PID or info.get("uid") != SELF_UID
                or "server.py" not in args
                or "--restart-helper" in args):
            continue
        candidates.append(pid)
    cwds = lsof_cwds(candidates)
    listener_map = {}
    for pid, port in scan_listeners():
        listener_map.setdefault(pid, []).append(port)
    result = []
    for pid in candidates:
        cwd = cwds.get(pid)
        try:
            same_dir = cwd and os.path.realpath(cwd) == os.path.realpath(BASE_DIR)
        except OSError:
            same_dir = False
        if not same_dir:
            continue
        info = snap.get(pid, {})
        result.append({
            "pid": pid,
            "ports": sorted(listener_map.get(pid, [])),
            "cmd": info.get("args") or "",
            "cwd": cwd,
            "uptimeSec": info.get("etime"),
        })
    return sorted(result, key=lambda item: (item["ports"] or [65536], item["pid"]))


def _launcher_dialog(message):
    script = """on run argv
set messageText to item 1 of argv
display dialog messageText with title "总控台" buttons {"取消", "重新启动", "打开控制台"} default button "打开控制台" cancel button "取消" with icon note
return button returned of result
end run"""
    try:
        result = subprocess.run(
            ["osascript", "-e", script, message], capture_output=True,
            text=True, timeout=180)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def _launcher_alert(message):
    script = """on run argv
display alert "总控台" message (item 1 of argv) as critical
end run"""
    try:
        subprocess.run(["osascript", "-e", script, message],
                       capture_output=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        pass


def launcher_main():
    """start.command 的无命令启动入口。"""
    instances = find_console_instances()
    if not instances:
        try:
            main(log_to_file=True)
        except Exception:
            _launcher_alert("总控台启动失败。请检查数据目录权限和 console.log。")
            raise
        return
    labels = []
    for item in instances:
        ports = " / ".join(":%d" % p for p in item["ports"]) or "未监听"
        labels.append("%s  ·  PID %d" % (ports, item["pid"]))
    extra = ("\n\n检测到 %d 个同项目实例，重启时会合并为一个。" % len(instances)
             if len(instances) > 1 else "")
    choice = _launcher_dialog(
        "总控台已在运行：\n" + "\n".join(labels) + extra)
    if choice == "打开控制台":
        ports = [p for item in instances for p in item["ports"]]
        port = min(ports) if ports else PORT_START
        webbrowser.open("http://%s:%d/" % (HOST, port))
        return
    if choice != "重新启动":
        return

    preferred_ports = [p for item in instances for p in item["ports"]]
    preferred = min(preferred_ports) if preferred_ports else PORT_START
    targets = [item["pid"] for item in instances]
    for pid in targets:
        if process_uid(pid) == SELF_UID:
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
    deadline = time.monotonic() + 8.0
    while time.monotonic() < deadline and any(pid_alive(pid) for pid in targets):
        time.sleep(0.1)
    survivors = [pid for pid in targets if pid_alive(pid)]
    if survivors:
        _launcher_alert("旧总控台未能正常退出（PID %s），未强制结束。" %
                        "、".join(str(pid) for pid in survivors))
        return
    try:
        main(preferred_port=preferred, log_to_file=True)
    except Exception:
        _launcher_alert("总控台重启失败。请检查数据目录权限和 console.log。")
        raise


def schedule_console_restart(server, preferred_port):
    """启动独立 helper，响应发出后关闭当前 HTTP 服务。"""
    helper = subprocess.Popen(
        [sys.executable, os.path.abspath(__file__), "--restart-helper",
         str(SELF_PID), str(int(preferred_port))],
        cwd=BASE_DIR, start_new_session=True, close_fds=True)

    def _shutdown():
        time.sleep(0.25)
        server.shutdown()
    threading.Thread(target=_shutdown, daemon=True).start()
    return helper.pid


def schedule_console_stop(server):
    """响应发送完成后关闭 HTTP 服务，不结束启动台里的独立进程组。"""
    def _shutdown():
        time.sleep(0.25)
        server.shutdown()
    threading.Thread(target=_shutdown, daemon=True).start()


def restart_helper(old_pid, preferred_port):
    """等旧进程释放端口后，在 helper 原地 exec 新总控台。"""
    deadline = time.monotonic() + 12.0
    while time.monotonic() < deadline and pid_alive(old_pid):
        time.sleep(0.1)
    if pid_alive(old_pid):
        return 1
    args = [sys.executable, os.path.abspath(__file__),
            "--preferred-port", str(int(preferred_port)), "--no-browser"]
    os.execv(sys.executable, args)
    return 0


def _run_console(preferred_port=None, open_browser=True):
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    for private_dir in (DATA_DIR, ICONS_DIR, LOGS_DIR):
        _ensure_private_dir(private_dir)
    start_log_maintenance()
    cfg = Config(CONFIG_PATH)

    server, port = None, None
    candidates = list(range(PORT_START, PORT_START + PORT_TRIES))
    if isinstance(preferred_port, int) and preferred_port in candidates:
        candidates.remove(preferred_port)
        candidates.insert(0, preferred_port)
    for p in candidates:
        try:
            server = ConsoleServer((HOST, p), Handler, cfg, p)
            port = p
            break
        except OSError:
            continue
    if server is None:
        print("错误：端口 %d-%d 均被占用，无法启动。" %
              (PORT_START, PORT_START + PORT_TRIES - 1))
        sys.exit(1)

    print("总控台已启动: http://%s:%d/  (Ctrl+C 停止)" % (HOST, port), flush=True)
    if open_browser:
        open_browser_later(port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        print("已停止", flush=True)


def redirect_console_output():
    """在运行目录迁移完成后，将 .app 输出安全追加到 Library Logs。"""
    path = os.path.join(LOGS_DIR, "console.log")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.fchmod(fd, 0o600)
        for stream in (sys.stdout, sys.stderr):
            try:
                stream.flush()
            except (AttributeError, OSError):
                pass
        os.dup2(fd, 1)
        os.dup2(fd, 2)
    finally:
        os.close(fd)
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(line_buffering=True)
        except (AttributeError, OSError):
            pass


def main(preferred_port=None, open_browser=True, log_to_file=False):
    """Run exactly one console for this project/data directory."""
    migration = prepare_runtime_storage()
    if log_to_file:
        redirect_console_output()
    if migration["dataMigrated"]:
        print("已将项目内旧配置和图标复制到: %s" % DATA_DIR,
              flush=True)
    if migration["logsMigrated"]:
        print("已将项目内旧日志复制到: %s" % LOGS_DIR,
              flush=True)
    instance_lock = acquire_instance_lock()
    if instance_lock is None:
        print("总控台已在运行（同一数据目录只允许一个实例）。", flush=True)
        if open_browser:
            instances = find_console_instances()
            ports = [port for item in instances for port in item.get("ports", [])]
            if ports:
                webbrowser.open("http://%s:%d/" % (HOST, min(ports)))
        return False
    try:
        _run_console(preferred_port, open_browser)
        return True
    finally:
        release_instance_lock(instance_lock)


if __name__ == "__main__":
    if "--prepare-storage" in sys.argv:
        # 供安装/诊断流程预先验证迁移和目录权限，不启动 HTTP。
        prepare_runtime_storage()
    elif "--launcher" in sys.argv:
        launcher_main()
    elif "--restart-helper" in sys.argv:
        index = sys.argv.index("--restart-helper")
        try:
            old = int(sys.argv[index + 1])
            preferred = int(sys.argv[index + 2])
        except (ValueError, IndexError):
            sys.exit(2)
        sys.exit(restart_helper(old, preferred))
    else:
        preferred = None
        if "--preferred-port" in sys.argv:
            index = sys.argv.index("--preferred-port")
            try:
                preferred = int(sys.argv[index + 1])
            except (ValueError, IndexError):
                sys.exit(2)
        main(preferred_port=preferred, open_browser="--no-browser" not in sys.argv)
