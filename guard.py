"""
StudyLock guard - the enforcement daemon.

Runs in the background for the whole length of a study session and:
  * kills any process on the blocklist (games, launchers, Discord desktop, ...)
  * serves the session status on http://127.0.0.1:47821/status for the browser extension
  * blackholes hard-blocked domains in the hosts file (only when running elevated)
  * keeps a twin copy of itself alive, and re-registers itself to run at logon

There is deliberately no stop, pause or exit path. The session ends when the
clock says it ends and not before.

Usage:  pythonw guard.py [A|B]
"""

import ctypes
import json
import os
import re
import subprocess
import sys
import threading
import time
from ctypes import wintypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

BASE = os.path.dirname(os.path.abspath(__file__))
STATE_PATH = os.path.join(BASE, "state.json")
RUN_DIR = os.path.join(BASE, "run")
LOG_PATH = os.path.join(RUN_DIR, "guard.log")
PORT = 47821
TICK = 1.0
PARTNER_TIMEOUT = 8.0
HOSTS_BEGIN = "# ==== StudyLock BEGIN - removed automatically when the session ends ===="
HOSTS_END = "# ==== StudyLock END ===="

CREATE_NO_WINDOW = 0x08000000
DETACHED_PROCESS = 0x00000008

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
psapi = ctypes.WinDLL("psapi", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PROCESS_TERMINATE = 0x0001


def log(msg):
    try:
        os.makedirs(RUN_DIR, exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write("%s  %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
    except OSError:
        pass


# --------------------------------------------------------------------------
# process enumeration / termination (pure ctypes, no third-party packages)
# --------------------------------------------------------------------------

def enum_pids():
    count = 4096
    while True:
        arr = (wintypes.DWORD * count)()
        needed = wintypes.DWORD()
        if not psapi.EnumProcesses(ctypes.byref(arr), ctypes.sizeof(arr), ctypes.byref(needed)):
            return []
        if needed.value >= ctypes.sizeof(arr):
            count *= 2
            continue
        return list(arr[: needed.value // ctypes.sizeof(wintypes.DWORD)])


def process_path(pid):
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        size = wintypes.DWORD(32768)
        buf = ctypes.create_unicode_buffer(size.value)
        if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return buf.value
        return None
    finally:
        kernel32.CloseHandle(handle)


def kill(pid):
    handle = kernel32.OpenProcess(PROCESS_TERMINATE, False, pid)
    if not handle:
        return False
    try:
        return bool(kernel32.TerminateProcess(handle, 1))
    finally:
        kernel32.CloseHandle(handle)


def is_admin():
    try:
        return bool(shell32.IsUserAnAdmin())
    except Exception:
        return False


# --------------------------------------------------------------------------
# session state
# --------------------------------------------------------------------------

def read_state():
    try:
        with open(STATE_PATH, "r", encoding="utf-8-sig") as fh:   # tolerate a BOM
            return json.load(fh)
    except (OSError, ValueError):
        return None


def write_state(state):
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2)
    os.replace(tmp, STATE_PATH)


def discord_guilds(cfg):
    """[(name, id)] of allowed servers. Accepts the old single-id key too."""
    out = []
    for g in cfg.get("discord_allowed_guilds") or []:
        gid = str(g.get("id", "")).strip()
        if re.fullmatch(r"[0-9]{5,25}", gid):
            out.append((str(g.get("name") or "Server"), gid))
    old = str(cfg.get("discord_allowed_guild_id", "") or "").strip()
    if re.fullmatch(r"[0-9]{5,25}", old) and old not in [g for _, g in out]:
        out.append(("IGCSE", old))
    return out


def web_rules(state):
    """Build the ruleset the browser extension enforces, from the frozen config."""
    cfg = state.get("rules", {})
    allowed = sorted({d.lower().lstrip(".").strip("/") for d in cfg.get("allowed_domains", [])})
    blocked = [d.lower().lstrip(".") for d in cfg.get("blocked_domains", [])]
    restricted = {}
    for dom, rule in (cfg.get("restricted_domains") or {}).items():
        restricted[dom.lower()] = {
            "label": rule.get("label", dom),
            "note": rule.get("note", ""),
            "allow_paths": rule.get("allow_paths", []),
            "deny_paths": rule.get("deny_paths", []),
            "links": rule.get("links", []),
        }

    # Invite links are a side door into other servers, always closed.
    blocked += ["discord.gg", "discord.me", "disboard.org"]

    guilds = discord_guilds(cfg)
    if guilds:
        rule = {
            "label": "Discord",
            "note": "Only your study servers (%s) are open during a session. DMs, other "
                    "servers and discovery are not." % ", ".join(n for n, _ in guilds),
            "allow_paths": ["^/channels/" + gid + "(/|$)" for _, gid in guilds],
            "deny_paths": [],
            # shown as buttons on the block page, so the way in is one click away
            "links": [{"name": n, "url": "https://discord.com/channels/" + gid} for n, gid in guilds],
        }
        restricted["discord.com"] = rule
        restricted["discordapp.com"] = dict(rule)
    else:
        blocked += ["discord.com", "discordapp.com"]

    # The allowlist wins outright: an allowed domain is never overlaid, and
    # never makes it into the hosts file either.
    def allowed_host(host):
        return any(host == a or host.endswith("." + a) for a in allowed)

    blocked = sorted({d for d in blocked if not allowed_host(d)})
    restricted = {d: r for d, r in restricted.items() if not allowed_host(d)}
    return {"allowed_domains": allowed, "blocked_domains": blocked,
            "restricted_domains": restricted}


def phase_at(state, remaining):
    """Which half of a Pomodoro cycle are we in?

    Returns (blocking, phase, seconds_left_in_phase). Elapsed time is derived
    from `remaining`, which already carries the monotonic floor, so winding the
    system clock forward cannot jump the session into a break.
    """
    if state.get("mode") != "pomodoro":
        return True, "focus", remaining
    focus = max(0.05, float(state.get("focus_min", 25))) * 60.0
    brk = max(0.05, float(state.get("break_min", 5))) * 60.0
    cycle = focus + brk
    total = max(0.0, state.get("end_wall", 0) - state.get("start_wall", 0))
    pos = max(0.0, total - remaining) % cycle
    if pos < focus:
        return True, "focus", min(focus - pos, remaining)
    return False, "break", min(cycle - pos, remaining)


# --------------------------------------------------------------------------
# hosts file layer (needs elevation; silently skipped otherwise)
# --------------------------------------------------------------------------

def hosts_path():
    return os.path.join(os.environ.get("SystemRoot", "C:/Windows"),
                        "System32", "drivers", "etc", "hosts")


def read_hosts():
    with open(hosts_path(), "r", encoding="utf-8", errors="replace") as fh:
        return fh.read()


def strip_block(text):
    out, skipping = [], False
    for line in text.splitlines():
        if line.strip() == HOSTS_BEGIN:
            skipping = True
            continue
        if line.strip() == HOSTS_END:
            skipping = False
            continue
        if not skipping:
            out.append(line)
    return "\n".join(out).rstrip() + "\n"


def apply_hosts(domains):
    if not is_admin():
        return False
    try:
        current = read_hosts()
    except OSError:
        return False
    body = [HOSTS_BEGIN]
    for dom in domains:
        body.append("127.0.0.1 " + dom)
        if not dom.startswith("www."):
            body.append("127.0.0.1 www." + dom)
        body.append("::1 " + dom)
    body.append(HOSTS_END)
    wanted = strip_block(current) + "\n".join(body) + "\n"
    if wanted == current:
        return True
    try:
        backup = os.path.join(RUN_DIR, "hosts.backup")
        if not os.path.exists(backup):
            os.makedirs(RUN_DIR, exist_ok=True)
            with open(backup, "w", encoding="utf-8") as fh:
                fh.write(strip_block(current))
        with open(hosts_path(), "w", encoding="utf-8") as fh:
            fh.write(wanted)
        flush_dns()
        return True
    except OSError as exc:
        log("hosts write failed: %s" % exc)
        return False


def clear_hosts():
    if not is_admin():
        return
    try:
        current = read_hosts()
        if HOSTS_BEGIN not in current:
            return
        with open(hosts_path(), "w", encoding="utf-8") as fh:
            fh.write(strip_block(current))
        flush_dns()
    except OSError as exc:
        log("hosts restore failed: %s" % exc)


def flush_dns():
    try:
        subprocess.run(["ipconfig", "/flushdns"], creationflags=CREATE_NO_WINDOW,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
    except Exception:
        pass


# --------------------------------------------------------------------------
# run-at-logon registration, so a reboot is not an escape hatch
# --------------------------------------------------------------------------

RUN_KEY = "Software/Microsoft/Windows/CurrentVersion/Run".replace("/", chr(92))
RUN_NAME = "StudyLock"


def pythonw():
    exe = sys.executable or ""
    if exe.lower().endswith("python.exe"):
        alt = exe[:-10] + "pythonw.exe"
        if os.path.exists(alt):
            return alt
    return exe


def set_run_at_logon(enabled):
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            if enabled:
                cmd = '"%s" "%s" A' % (pythonw(), os.path.join(BASE, "guard.py"))
                winreg.SetValueEx(key, RUN_NAME, 0, winreg.REG_SZ, cmd)
            else:
                try:
                    winreg.DeleteValue(key, RUN_NAME)
                except FileNotFoundError:
                    pass
    except OSError as exc:
        log("run key update failed: %s" % exc)


# --------------------------------------------------------------------------
# status server for the browser extension
# --------------------------------------------------------------------------

class Status:
    lock = threading.Lock()
    payload = {"active": False}

    @classmethod
    def set(cls, payload):
        with cls.lock:
            cls.payload = payload

    @classmethod
    def get(cls):
        with cls.lock:
            return dict(cls.payload)


class Shield:
    """Last time each browser's extension checked in. If a browser is running
    and its shield has gone quiet, the extension was removed or disabled.

    Only twin A runs the HTTP server, so the timestamp is shared through a file
    - otherwise twin B would never see a heartbeat and would close the browser
    even with the extension healthy.
    """
    lock = threading.Lock()
    last = {}

    @staticmethod
    def _clean(vendor):
        # Comes straight off a URL and is used as a filename: keep it boring.
        vendor = re.sub(r"[^a-z0-9_-]", "", str(vendor).lower())[:24]
        return vendor or "chrome"

    @classmethod
    def _path(cls, vendor):
        return os.path.join(RUN_DIR, "shield_%s" % cls._clean(vendor))

    @classmethod
    def ping(cls, vendor):
        vendor = cls._clean(vendor)
        now = time.time()
        with cls.lock:
            cls.last[vendor] = now
        try:
            os.makedirs(RUN_DIR, exist_ok=True)
            with open(cls._path(vendor), "w", encoding="utf-8") as fh:
                fh.write(str(now))
        except OSError:
            pass

    @classmethod
    def seen(cls, vendor):
        vendor = cls._clean(vendor)
        with cls.lock:
            ts = cls.last.get(vendor, 0.0)
        try:
            with open(cls._path(vendor), "r", encoding="utf-8") as fh:
                ts = max(ts, float(fh.read().strip()))
        except (OSError, ValueError):
            pass
        return ts


class StatusServer(ThreadingHTTPServer):
    # Threaded, so one stale client cannot wedge the endpoint. Address reuse
    # stays ON so a restarted guard rebinds instantly instead of waiting out
    # TIME_WAIT - a gap there would leave the browser unshielded. Only twin A
    # ever serves, so nothing double-binds.
    allow_reuse_address = True
    daemon_threads = True


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"   # every response closes; no keep-alive to hang on
    timeout = 10

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path not in ("/status", "/"):
            self.send_error(404)
            return
        query = parse_qs(parsed.query)
        if query.get("src") == ["shield"]:
            Shield.ping((query.get("b") or ["chrome"])[0])
        body = json.dumps(Status.get()).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def serve():
    try:
        httpd = StatusServer(("127.0.0.1", PORT), Handler)
    except OSError:
        return  # the twin already owns the port
    httpd.serve_forever()


# --------------------------------------------------------------------------
# twin watchdog
# --------------------------------------------------------------------------

def heartbeat(role):
    try:
        os.makedirs(RUN_DIR, exist_ok=True)
        with open(os.path.join(RUN_DIR, "hb_%s" % role), "w", encoding="utf-8") as fh:
            fh.write(str(time.time()))
    except OSError:
        pass


def partner_alive(role):
    try:
        with open(os.path.join(RUN_DIR, "hb_%s" % role), "r", encoding="utf-8") as fh:
            return time.time() - float(fh.read().strip()) < PARTNER_TIMEOUT
    except (OSError, ValueError):
        return False


def spawn(role):
    try:
        subprocess.Popen([pythonw(), os.path.join(BASE, "guard.py"), role],
                         creationflags=CREATE_NO_WINDOW | DETACHED_PROCESS,
                         close_fds=True)
        log("respawned twin %s" % role)
    except Exception as exc:
        log("respawn failed: %s" % exc)


def single_instance(role):
    """Returns False if another guard with this role already runs."""
    name = "Local" + chr(92) + "StudyLockGuard_" + role
    handle = kernel32.CreateMutexW(None, True, name)
    if not handle or ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
        return False
    single_instance._handle = handle
    return True


# --------------------------------------------------------------------------
# enforcement
# --------------------------------------------------------------------------

WINDOWS_DIR = (os.environ.get("SystemRoot", "C:/Windows") + os.sep).lower().replace("/", os.sep)


def compile_rules(rules):
    return (
        {n.lower() for n in rules.get("blocked_processes", [])},
        [f.lower().replace(chr(92), "/") for f in rules.get("blocked_path_fragments", [])],
        {n.lower() for n in rules.get("process_exceptions", [])}
        | {n.lower() for n in rules.get("allowed_processes", [])},
    )


def should_kill(path, names, fragments, exceptions):
    """Single source of truth for 'is this process blocked'."""
    low = path.lower()
    if low.startswith(WINDOWS_DIR):
        return False                      # never touch the OS
    name = os.path.basename(low)
    if name in exceptions or name in ("python.exe", "pythonw.exe"):
        return False                      # allowlist wins, and never suicide
    slashed = low.replace(chr(92), "/")
    return name in names or any(fragment in slashed for fragment in fragments)


def snapshot(self_pids):
    """One process enumeration per tick, shared by both enforcers."""
    out = []
    for pid in enum_pids():
        if pid in self_pids or pid <= 4:
            continue
        path = process_path(pid)
        if path:
            out.append((pid, path))
    return out


def enforce_processes(rules, procs):
    names, fragments, exceptions = compile_rules(rules)
    killed = []
    for pid, path in procs:
        if should_kill(path, names, fragments, exceptions) and kill(pid):
            killed.append(os.path.basename(path.lower()))
    if killed:
        log("terminated: %s" % ", ".join(sorted(set(killed))))


def enforce_shield(rules, procs, seen_since, now):
    """Close any shielded browser whose extension has stopped reporting in.

    Removing or disabling the extension does not unlock the browser - it makes
    the browser unusable for the rest of the session.
    """
    if not rules.get("require_browser_shield", True):
        return []
    mapping = {k.lower(): v for k, v in (rules.get("shielded_browsers") or {}).items()}
    launch_grace = float(rules.get("shield_grace_seconds", 25))
    timeout = float(rules.get("shield_timeout_seconds", 90))

    running = {}
    for pid, path in procs:
        low = path.lower()
        name = os.path.basename(low)
        if name not in mapping:
            continue
        if "edgewebview" in low or "edgecore" in low:
            continue          # WebView2 runtime - Teams and others need it
        running.setdefault(name, []).append(pid)

    for exe in [e for e in seen_since if e not in running]:
        seen_since.pop(exe, None)   # browser closed; start its clock fresh

    killed = []
    for exe, pids in running.items():
        seen_since.setdefault(exe, now)
        if now - seen_since[exe] < launch_grace:
            continue            # just launched, give the extension time to call in
        last = Shield.seen(mapping[exe])
        if last <= 0:
            # This browser's shield has never once checked in, so the extension
            # was never installed here. Enforcing would just close a browser the
            # user is entitled to. The shield arms itself the first time it
            # reports in, and only then can going quiet close the browser.
            continue
        if now - last > timeout:
            if sum(1 for pid in pids if kill(pid)):
                killed.append(exe)
    if killed:
        log("shield missing, closed: %s" % ", ".join(sorted(set(killed))))
    return killed


def main():
    role = sys.argv[1].upper() if len(sys.argv) > 1 and sys.argv[1].upper() in ("A", "B") else "A"
    twin = "B" if role == "A" else "A"
    if not single_instance(role):
        return
    log("guard %s started (admin=%s)" % (role, is_admin()))

    if role == "A":
        threading.Thread(target=serve, daemon=True).start()

    mono_deadline = None
    session_id = None
    hosts_applied = False
    self_pids = {os.getpid()}
    idle_since = time.time()
    cleaned = False
    browser_seen = {}
    last_phase = None

    while True:
        heartbeat(role)
        state = read_state()
        now = time.time()

        if state and state.get("active"):
            if session_id != state.get("id"):
                session_id = state.get("id")
                mono_deadline = time.monotonic() + max(0.0, state.get("end_wall", 0) - now)
                hosts_applied = False
                last_phase = None
                set_run_at_logon(True)
                log("session %s picked up, %.0f s left" % (session_id, state["end_wall"] - now))

            # A session ends only when BOTH the wall clock and a monotonic
            # timer agree - winding the system clock forward does nothing.
            wall_left = state.get("end_wall", 0) - now
            mono_left = mono_deadline - time.monotonic()
            remaining = max(wall_left, mono_left)

            if remaining > 0:
                idle_since = None
                if not partner_alive(twin):
                    spawn(twin)
                rules = state.get("rules", {})
                blocking, phase, phase_left = phase_at(state, remaining)
                web = web_rules(state)
                shield_down = []

                if blocking:
                    procs = snapshot(self_pids)
                    enforce_processes(rules, procs)
                    shield_down = enforce_shield(rules, procs, browser_seen, now)
                    if rules.get("hosts_layer", True) and not hosts_applied:
                        hosts_applied = apply_hosts(web["blocked_domains"])
                else:
                    # Break: everything comes back. Nothing is killed, the
                    # browser shield stands down, and the DNS block lifts.
                    if hosts_applied:
                        clear_hosts()
                        hosts_applied = False
                    browser_seen.clear()

                if phase != last_phase:
                    log("phase -> %s (%.0f s)" % (phase, phase_left))
                    last_phase = phase

                Status.set({
                    "active": True,
                    "remaining": int(remaining),
                    "end_wall": state.get("end_wall"),
                    "label": state.get("label", ""),
                    "hardened": bool(hosts_applied),
                    "shield_down": shield_down,
                    "blocking": bool(blocking),
                    "phase": phase,
                    "phase_remaining": int(phase_left),
                    "rules": web,
                })

                # a deleted state file does not shorten anything
                if not os.path.exists(STATE_PATH):
                    write_state(state)
            else:
                finish(state)
                session_id = None
                hosts_applied = False
                cleaned = True
                idle_since = time.time()
        else:
            Status.set({"active": False})
            if not cleaned:
                clear_hosts()   # tidy up after a crash or a forced reboot
                set_run_at_logon(False)
                cleaned = True
            if idle_since is None:
                idle_since = now
            # No session to guard. Exit quickly and cheaply - the scheduled task
            # (or the app) starts us again the moment one exists.
            if now - idle_since > 10:
                log("guard %s idle, exiting" % role)
                return

        time.sleep(TICK)


def finish(state):
    latest = read_state()
    if latest and not latest.get("active"):
        return  # the twin got there first
    log("session complete")
    state["active"] = False
    state["completed_wall"] = time.time()
    write_state(state)
    clear_hosts()
    set_run_at_logon(False)
    Status.set({"active": False})
    try:
        subprocess.Popen([pythonw(), os.path.join(BASE, "studylock.pyw"), "--done"],
                         creationflags=CREATE_NO_WINDOW | DETACHED_PROCESS, close_fds=True)
    except Exception:
        pass


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # never die quietly
        log("crash: %r" % exc)
        raise
