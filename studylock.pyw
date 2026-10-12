"""
StudyLock - the front end.

Pick a duration, press start. From that moment the guard daemon owns the
session: there is no stop button, no pause, no "just 5 more minutes". Closing
this window does not end anything - it only hides the clock.
"""

import json
import os
import random
import shutil
import subprocess
import sys
import time
import tkinter as tk
import urllib.request
from tkinter import font as tkfont
from tkinter import messagebox

BASE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE, "config.json")
CONFIG_TEMPLATE = os.path.join(BASE, "config.example.json")
STATE_PATH = os.path.join(BASE, "state.json")
GUARD = os.path.join(BASE, "guard.py")
TASK_NAME = "StudyLock Guard"
PORT = 47821

CREATE_NO_WINDOW = 0x08000000
DETACHED_PROCESS = 0x00000008

BG = "#0b0f14"
CARD = "#121a26"
LINE = "#1e2a3a"
FG = "#e8eef7"
MUTED = "#64798f"
DIM = "#9fb3c8"
ACCENT = "#7dd3fc"
WARN = "#f0a6a6"
OK = "#86efac"

PRESETS = [("15 min", 15), ("30 min", 30), ("45 min", 45),
           ("1 hour", 60), ("1h 30m", 90), ("2 hours", 120)]

CHIME_PATH = os.path.join(BASE, "run", "complete.wav")


# ------------------------------------------------------------------- the sound

def build_chime(path=CHIME_PATH):
    """A rising major arpeggio with bell-like decay, written once to disk."""
    import array
    import math
    import wave

    rate = 44100
    notes = [(1046.50, 0.00), (1318.51, 0.15), (1567.98, 0.30), (2093.00, 0.45)]
    total = 2.6
    count = int(rate * total)
    buf = [0.0] * count

    for freq, start in notes:
        offset = int(start * rate)
        for i in range(offset, count):
            t = (i - offset) / rate
            decay = math.exp(-2.6 * t)
            if decay < 1e-4:
                break
            attack = 1.0 - math.exp(-t * 260.0)     # soft edge, no click
            env = decay * attack
            v = math.sin(2 * math.pi * freq * t) * 0.60
            v += math.sin(2 * math.pi * freq * 2 * t) * 0.17
            v += math.sin(2 * math.pi * freq * 3 * t) * 0.07
            buf[i] += v * env

    peak = max(abs(x) for x in buf) or 1.0
    scale = 0.86 / peak
    data = array.array("h", (int(max(-1.0, min(1.0, x * scale)) * 32767) for x in buf))

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with wave.open(path, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(data.tobytes())
    return path


def play_chime():
    try:
        import winsound
    except ImportError:
        return
    try:
        if not os.path.exists(CHIME_PATH):
            build_chime()
        winsound.PlaySound(CHIME_PATH, winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
    except Exception:
        try:
            winsound.MessageBeep()
        except Exception:
            pass


# ------------------------------------------------------------------ helpers

def load_config():
    # First run on a new machine: start from the shipped template so the repo
    # never has to carry anyone's personal server IDs.
    if not os.path.exists(CONFIG_PATH) and os.path.exists(CONFIG_TEMPLATE):
        shutil.copyfile(CONFIG_TEMPLATE, CONFIG_PATH)
    with open(CONFIG_PATH, "r", encoding="utf-8-sig") as fh:
        return json.load(fh)


def save_config(cfg):
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=2)
    os.replace(tmp, CONFIG_PATH)


def load_state():
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def write_state(state):
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2)
    os.replace(tmp, STATE_PATH)


def session_remaining(state):
    if not state or not state.get("active"):
        return 0
    return max(0, state.get("end_wall", 0) - time.time())


def pythonw():
    exe = sys.executable or ""
    if exe.lower().endswith("python.exe"):
        alt = exe[:-10] + "pythonw.exe"
        if os.path.exists(alt):
            return alt
    return exe


def guard_alive():
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/status" % PORT, timeout=1) as res:
            return json.load(res)
    except Exception:
        return None


def start_guard():
    """Prefer the elevated scheduled task; fall back to a plain background process."""
    try:
        probe = subprocess.run(["schtasks", "/query", "/tn", TASK_NAME],
                               creationflags=CREATE_NO_WINDOW, capture_output=True, timeout=10)
        if probe.returncode == 0:
            subprocess.run(["schtasks", "/run", "/tn", TASK_NAME],
                           creationflags=CREATE_NO_WINDOW, capture_output=True, timeout=10)
            time.sleep(0.6)
            if guard_alive() is not None:
                return
    except Exception:
        pass
    try:
        subprocess.Popen([pythonw(), GUARD, "A"],
                         creationflags=CREATE_NO_WINDOW | DETACHED_PROCESS, close_fds=True)
    except Exception as exc:
        messagebox.showerror("StudyLock", "Could not start the blocker:\n%s" % exc)


def effective_rules(cfg):
    """The rules the browser actually enforces - same code path as the guard,
    so the counts and the list can never drift from what gets blocked."""
    try:
        if BASE not in sys.path:
            sys.path.insert(0, BASE)
        import guard
        return guard.web_rules({"rules": cfg})
    except Exception:
        return {"blocked_domains": sorted(cfg.get("blocked_domains", [])),
                "restricted_domains": cfg.get("restricted_domains", {}) or {},
                "allowed_domains": cfg.get("allowed_domains", [])}


def phase_of(state, remaining):
    """Ask the guard's own phase function, so the clock and the enforcement
    can never disagree about whether you are on a break."""
    try:
        if BASE not in sys.path:
            sys.path.insert(0, BASE)
        import guard
        return guard.phase_at(state, remaining)
    except Exception:
        return True, "focus", remaining


def fmt(seconds):
    seconds = int(max(0, seconds))
    h, m, s = seconds // 3600, (seconds % 3600) // 60, seconds % 60
    return "%d:%02d:%02d" % (h, m, s) if h else "%02d:%02d" % (m, s)


# ------------------------------------------------------------------- the app

class App:
    def __init__(self, root, show_done=False):
        self.root = root
        root.title("StudyLock")
        root.configure(bg=BG)
        root.resizable(False, False)
        self.center(460, 600)

        self.f_display = tkfont.Font(family="Segoe UI Light", size=58)
        self.f_h1 = tkfont.Font(family="Segoe UI Semibold", size=19)
        self.f_body = tkfont.Font(family="Segoe UI", size=10)
        self.f_small = tkfont.Font(family="Segoe UI", size=9)
        self.f_badge = tkfont.Font(family="Segoe UI Semibold", size=8)
        self.f_btn = tkfont.Font(family="Segoe UI Semibold", size=11)
        self.f_link = tkfont.Font(family="Segoe UI", size=9, underline=True)

        self.container = tk.Frame(root, bg=BG)
        self.container.pack(fill="both", expand=True)
        self.minutes = tk.IntVar(value=45)
        self.pomodoro = tk.BooleanVar(value=False)
        self.label_var = tk.StringVar()

        state = load_state()
        if session_remaining(state) > 0:
            self.locked_view(state)
        elif show_done and state:
            self.done_view(state)
        else:
            self.setup_view()

    def center(self, w, h):
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        self.root.geometry("%dx%d+%d+%d" % (w, h, (sw - w) // 2, (sh - h) // 3))

    def fit(self, min_h=600):
        """Size the window to the view's content so nothing is ever cut off."""
        self.root.update_idletasks()
        need = self.container.winfo_reqheight() + 8
        cap = self.root.winfo_screenheight() - 90
        h = max(min_h, min(need, cap))
        w = 460
        x = (self.root.winfo_screenwidth() - w) // 2
        y = max(10, (self.root.winfo_screenheight() - h) // 3)
        self.root.geometry("%dx%d+%d+%d" % (w, h, x, y))

    def clear(self):
        if getattr(self, "after_id", None):
            try:
                self.root.after_cancel(self.after_id)
            except tk.TclError:
                pass
            self.after_id = None
        for child in self.container.winfo_children():
            child.destroy()

    def badge(self, parent, text, colour=ACCENT):
        f = tk.Frame(parent, bg=BG)
        tk.Label(f, text=text.upper(), font=self.f_badge, bg=BG, fg=colour).pack()
        return f

    # ----------------------------------------------------------- setup view

    def setup_view(self):
        self.clear()
        c = self.container
        tk.Frame(c, bg=BG, height=20).pack()
        self.badge(c, "study session").pack()
        tk.Label(c, text="How long are you working?", font=self.f_h1, bg=BG, fg=FG).pack(pady=(10, 4))
        tk.Label(c, text="Once it starts, it cannot be stopped early.",
                 font=self.f_body, bg=BG, fg=MUTED).pack()

        grid = tk.Frame(c, bg=BG)
        grid.pack(pady=(18, 4))
        self.preset_buttons = {}
        for i, (text, mins) in enumerate(PRESETS):
            b = tk.Label(grid, text=text, font=self.f_body, bg=CARD, fg=DIM,
                         width=9, pady=9, cursor="hand2")
            b.grid(row=i // 3, column=i % 3, padx=4, pady=4)
            b.bind("<Button-1>", lambda e, m=mins: self.pick(m))
            self.preset_buttons[mins] = b

        custom = tk.Frame(c, bg=BG)
        custom.pack(pady=(10, 0))
        tk.Label(custom, text="or", font=self.f_small, bg=BG, fg=MUTED).pack(side="left", padx=(0, 8))
        self.custom_entry = tk.Entry(custom, width=5, font=self.f_body, bg=CARD, fg=FG,
                                     insertbackground=FG, relief="flat", justify="center")
        self.custom_entry.pack(side="left", ipady=5)
        self.custom_entry.bind("<KeyRelease>", self.custom_changed)
        tk.Label(custom, text="minutes", font=self.f_small, bg=BG, fg=MUTED).pack(side="left", padx=8)

        self.pom_lbl = tk.Label(c, text="", font=self.f_small, bg=BG, fg=MUTED, cursor="hand2")
        self.pom_lbl.pack(pady=(12, 0))
        self.pom_lbl.bind("<Button-1>", lambda e: self.toggle_pomodoro())
        self.pom_note = tk.Label(c, text="", font=self.f_small, bg=BG, fg=MUTED)
        self.pom_note.pack()
        self.paint_pomodoro()

        tk.Label(c, text="WHAT ARE YOU WORKING ON?  (OPTIONAL)", font=self.f_badge,
                 bg=BG, fg=MUTED).pack(pady=(16, 6))
        entry = tk.Entry(c, textvariable=self.label_var, font=self.f_body, bg=CARD, fg=FG,
                         insertbackground=FG, relief="flat", justify="center", width=34)
        entry.pack(ipady=7)

        cfg = load_config()
        web = effective_rules(cfg)
        n_apps = len(cfg.get("blocked_processes", []))
        n_sites = len(web.get("blocked_domains", [])) + len(web.get("restricted_domains", {}))

        box = tk.Frame(c, bg=BG)
        box.pack(pady=(16, 0))
        link = tk.Label(box, text="%d apps and %d sites will be blocked" % (n_apps, n_sites),
                        font=self.f_link, bg=BG, fg=ACCENT, cursor="hand2")
        link.pack()
        link.bind("<Button-1>", lambda e: self.blocklist_view(self.setup_view))
        self.discord_row(box).pack(pady=(6, 0))
        tk.Label(box, text="YouTube limited to search and video pages",
                 font=self.f_small, bg=BG, fg=MUTED).pack()
        tk.Label(box, text="%d study apps and %d study sites always stay open"
                          % (len(cfg.get("allowed_processes", [])), len(cfg.get("allowed_domains", []))),
                 font=self.f_small, bg=BG, fg=MUTED).pack()

        self.start_btn = tk.Label(c, text="START LOCK", font=self.f_btn, bg=ACCENT, fg="#06121c",
                                  width=22, pady=12, cursor="hand2")
        self.start_btn.pack(pady=(16, 0))
        self.start_btn.bind("<Button-1>", lambda e: self.start())

        foot = tk.Frame(c, bg=BG)
        foot.pack(side="bottom", pady=10)
        link = tk.Label(foot, text="settings", font=self.f_small, bg=BG, fg=MUTED, cursor="hand2")
        link.pack(side="left", padx=10)
        link.bind("<Button-1>", lambda e: self.settings_view())
        setup = tk.Label(foot, text="browser setup", font=self.f_small, bg=BG, fg=MUTED, cursor="hand2")
        setup.pack(side="left", padx=10)
        setup.bind("<Button-1>", lambda e: self.open_setup())

        self.fit()
        self.pick(self.minutes.get())

    def guilds(self):
        try:
            if BASE not in sys.path:
                sys.path.insert(0, BASE)
            import guard
            return guard.discord_guilds(load_config())
        except Exception:
            return []

    def copy_discord(self, widget, gid):
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append("https://discord.com/channels/" + gid)
            self.root.update_idletasks()
        except tk.TclError:
            return
        widget.configure(text="copied", fg=OK)
        self.root.after(1800, lambda: widget.winfo_exists()
                        and widget.configure(text="copy link", fg=ACCENT))

    def discord_row(self, parent, bg=BG):
        """One line per allowed server, each with a copy button - the rest of
        Discord is blocked, so these deep links are the only way in."""
        box = tk.Frame(parent, bg=bg)
        guilds = self.guilds()
        if not guilds:
            tk.Label(box, text="Discord fully blocked (no server set)",
                     font=self.f_small, bg=bg, fg=MUTED).pack()
            return box
        for name, gid in guilds:
            row = tk.Frame(box, bg=bg)
            row.pack()
            tk.Label(row, text="Discord: %s server open" % name,
                     font=self.f_small, bg=bg, fg=MUTED).pack(side="left")
            btn = tk.Label(row, text="copy link", font=self.f_link, bg=bg,
                           fg=ACCENT, cursor="hand2")
            btn.pack(side="left", padx=(9, 0))
            btn.bind("<Button-1>", lambda e, b=btn, g=gid: self.copy_discord(b, g))
        return box

    # -------------------------------------------------------- blocklist view

    def blocklist_view(self, back_to):
        self.clear()
        cfg = load_config()
        web = effective_rules(cfg)
        c = self.container

        tk.Frame(c, bg=BG, height=22).pack()
        self.badge(c, "everything blocked").pack()
        tk.Label(c, text="The full list", font=self.f_h1, bg=BG, fg=FG).pack(pady=(10, 2))
        tk.Label(c, text="Alphabetical. Frozen into a session the moment it starts.",
                 font=self.f_small, bg=BG, fg=MUTED).pack(pady=(0, 14))

        wrap = tk.Frame(c, bg=BG)
        wrap.pack(fill="both", expand=True, padx=26)
        bar = tk.Scrollbar(wrap)
        bar.pack(side="right", fill="y")
        txt = tk.Text(wrap, bg=CARD, fg=DIM, relief="flat", font=self.f_small, wrap="none",
                      yscrollcommand=bar.set, padx=16, pady=12, highlightthickness=0,
                      cursor="arrow", spacing1=1)
        txt.pack(side="left", fill="both", expand=True)
        bar.configure(command=txt.yview, bg=CARD, troughcolor=BG, relief="flat",
                      borderwidth=0, highlightthickness=0)
        txt.tag_configure("head", foreground=ACCENT, font=self.f_badge,
                          spacing1=14, spacing3=7)
        txt.tag_configure("num", foreground=MUTED)
        txt.tag_configure("note", foreground=MUTED, spacing3=4)

        def section(title, items):
            txt.insert("end", "%s  (%d)\n" % (title.upper(), len(items)), "head")
            for i, item in enumerate(items, 1):
                txt.insert("end", "%3d.  " % i, "num")
                txt.insert("end", "%s\n" % item)

        section("Apps closed on sight", sorted(cfg.get("blocked_processes", [])))
        section("Sites blocked outright", sorted(web.get("blocked_domains", [])))

        partial = []
        for dom, rule in sorted((web.get("restricted_domains") or {}).items()):
            allowed = ", ".join(p.lstrip("^") for p in rule.get("allow_paths", [])) or "nothing"
            partial.append("%s  -  only %s" % (dom, allowed))
        section("Partly blocked", partial)

        txt.insert("end", "\nAlso blocked: anything launched from %d game and browser "
                          "install folders, so new installs are covered automatically.\n"
                   % len(cfg.get("blocked_path_fragments", [])), "note")
        txt.configure(state="disabled")

        back = tk.Label(c, text="back", font=self.f_link, bg=BG, fg=MUTED, cursor="hand2")
        back.pack(pady=14)
        back.bind("<Button-1>", lambda e: back_to())

    def toggle_pomodoro(self):
        self.pomodoro.set(not self.pomodoro.get())
        self.paint_pomodoro()

    def paint_pomodoro(self):
        on = self.pomodoro.get()
        self.pom_lbl.configure(text=("%s  Pomodoro  -  25 min work, 5 min break"
                                     % ("●" if on else "○")),
                               fg=ACCENT if on else MUTED)
        self.pom_note.configure(
            text="Apps and sites unblock during every break." if on else "")

    def pick(self, mins):
        self.minutes.set(mins)
        self.custom_entry.delete(0, "end")
        for m, btn in self.preset_buttons.items():
            chosen = (m == mins)
            btn.configure(bg=ACCENT if chosen else CARD, fg="#06121c" if chosen else DIM)

    def custom_changed(self, _event):
        raw = self.custom_entry.get().strip()
        for btn in self.preset_buttons.values():
            btn.configure(bg=CARD, fg=DIM)
        if raw.isdigit() and 0 < int(raw) <= 720:
            self.minutes.set(int(raw))

    def start(self):
        mins = self.minutes.get()
        raw = self.custom_entry.get().strip()
        if raw:
            if not raw.isdigit() or not (1 <= int(raw) <= 720):
                messagebox.showwarning("StudyLock", "Enter a whole number of minutes between 1 and 720.")
                return
            mins = int(raw)
        if mins < 1:
            return

        ok = messagebox.askyesno(
            "Lock in for %d minutes?" % mins,
            (("Work for 25 minutes, break for 5, on repeat for the next %d minutes.\n"
              "Everything unblocks during each break.\n\n") if self.pomodoro.get() else
             ("Your games, launchers, Discord app and distracting sites will be blocked "
              "for the next %d minutes.\n\n")) % mins +
            "There is no stop button, no pause, and no bypass. Restarting the computer "
            "does not end it either.\n\nStart now?",
            icon="warning")
        if not ok:
            return

        cfg = load_config()
        now = time.time()
        state = {
            "id": "%d-%04d" % (int(now), random.randint(0, 9999)),
            "active": True,
            "label": self.label_var.get().strip(),
            "duration_min": mins,
            "start_wall": now,
            "end_wall": now + mins * 60,
            "mode": "pomodoro" if self.pomodoro.get() else "straight",
            "focus_min": 25,
            "break_min": 5,
            # Frozen copy: editing config.json mid-session changes nothing.
            "rules": cfg,
        }
        write_state(state)
        start_guard()
        self.locked_view(state)

    # ---------------------------------------------------------- locked view

    def locked_view(self, state):
        self.clear()
        c = self.container
        tk.Frame(c, bg=BG, height=50).pack()
        self.badge_lbl = tk.Label(c, text="LOCKED IN", font=self.f_badge, bg=BG, fg=ACCENT)
        self.badge_lbl.pack()

        self.clock_lbl = tk.Label(c, text="--:--", font=self.f_display, bg=BG, fg=FG)
        self.clock_lbl.pack(pady=(16, 0))
        self.phase_lbl = tk.Label(c, text="REMAINING", font=self.f_badge, bg=BG, fg=MUTED)
        self.phase_lbl.pack()
        self.sub_lbl = tk.Label(c, text="", font=self.f_small, bg=BG, fg=MUTED)
        self.sub_lbl.pack(pady=(7, 0))
        self.last_phase = None

        label = state.get("label", "")
        if label:
            tk.Label(c, text=label, font=self.f_h1, bg=BG, fg=ACCENT,
                     wraplength=380).pack(pady=(28, 0))

        self.discord_row(c).pack(pady=(22, 0))

        card = tk.Frame(c, bg=CARD)
        card.pack(fill="x", padx=34, pady=(18, 0))
        self.status_lbl = tk.Label(card, text="", font=self.f_small, bg=CARD, fg=DIM,
                                   justify="left", anchor="w")
        self.status_lbl.pack(fill="x", padx=18, pady=16)

        tk.Label(c, text="No stop button. No pause. No bypass.\nClosing this window changes nothing.",
                 font=self.f_small, bg=BG, fg=MUTED, justify="center").pack(side="bottom", pady=22)

        self.state = state
        self.fit()
        self.tick()

    def tick(self):
        state = load_state() or self.state
        left = session_remaining(state)
        if left <= 0:
            fresh = load_state()
            if fresh and fresh.get("active") and session_remaining(fresh) > 0:
                self.state = fresh
                left = session_remaining(fresh)
            else:
                self.done_view(state)
                return
        self.state = state
        blocking, phase, phase_left = phase_of(state, left)
        pomo = state.get("mode") == "pomodoro"
        if self.last_phase is not None and phase != self.last_phase:
            play_chime()                      # the break bell, and the bell back
        self.last_phase = phase

        if pomo and not blocking:
            self.badge_lbl.configure(text="BREAK · EVERYTHING UNBLOCKED", fg=OK)
            self.clock_lbl.configure(text=fmt(phase_left), fg=OK)
            self.phase_lbl.configure(text="UNTIL WORK RESUMES")
        elif pomo:
            self.badge_lbl.configure(text="LOCKED IN", fg=ACCENT)
            self.clock_lbl.configure(text=fmt(phase_left), fg=FG)
            self.phase_lbl.configure(text="UNTIL BREAK")
        else:
            self.clock_lbl.configure(text=fmt(left), fg=FG)
        self.sub_lbl.configure(text=("session ends in " + fmt(left)) if pomo else "")

        status = guard_alive()
        if status is None:
            self.status_lbl.configure(
                text="Blocker not responding - restarting it now.\n"
                     "Sites stay blocked in the browser regardless.", fg=WARN)
            start_guard()
        else:
            rules = status.get("rules", {})
            sites = len(rules.get("blocked_domains", [])) + len(rules.get("restricted_domains", {}))
            apps = len(state.get("rules", {}).get("blocked_processes", []))
            hardened = "hardened (system-wide DNS block active)" if status.get("hardened") \
                else "standard (browser extension + app blocking)"
            text = ("Blocker running\n%d apps terminated on sight\n%d sites shielded\nMode: %s"
                    % (apps, sites, hardened))
            if not status.get("blocking", True):
                self.status_lbl.configure(
                    text="Break in progress\nEvery app and site is unblocked\n"
                         "Blocking resumes automatically", fg=OK)
                self.after_id = self.root.after(500, self.tick)
                return
            down = status.get("shield_down") or []
            if down:
                self.status_lbl.configure(
                    text=text + "\n\nBrowser shield missing in %s.\nIt will not stay open until the "
                                "extension is back." % ", ".join(down), fg=WARN)
            else:
                self.status_lbl.configure(text=text, fg=DIM)
        self.after_id = self.root.after(500, self.tick)

    # ------------------------------------------------------------ done view

    def done_view(self, state):
        self.clear()
        play_chime()
        c = self.container
        tk.Frame(c, bg=BG, height=90).pack()
        self.badge(c, "session complete", ACCENT).pack()
        tk.Label(c, text="Done.", font=self.f_display, bg=BG, fg=FG).pack(pady=(10, 0))
        mins = int(state.get("duration_min", 0)) if state else 0
        label = (state or {}).get("label", "")
        text = "You studied for %d minutes." % mins
        if label:
            text += "\n%s" % label
        tk.Label(c, text=text, font=self.f_body, bg=BG, fg=DIM, justify="center").pack(pady=(18, 0))
        tk.Label(c, text="Everything is unblocked again.", font=self.f_small,
                 bg=BG, fg=MUTED).pack(pady=(8, 0))
        btn = tk.Label(c, text="NEW SESSION", font=self.f_btn, bg=ACCENT, fg="#06121c",
                       width=22, pady=12, cursor="hand2")
        btn.pack(pady=36)
        btn.bind("<Button-1>", lambda e: self.setup_view())

    # -------------------------------------------------------- settings view

    def settings_view(self):
        if session_remaining(load_state()) > 0:
            return
        self.clear()
        cfg = load_config()
        c = self.container
        tk.Frame(c, bg=BG, height=26).pack()
        self.badge(c, "settings").pack()
        tk.Label(c, text="Rules", font=self.f_h1, bg=BG, fg=FG).pack(pady=(12, 2))
        tk.Label(c, text="Only editable between sessions.", font=self.f_small,
                 bg=BG, fg=MUTED).pack()

        tk.Label(c, text="ALLOWED DISCORD SERVER IDS  (COMMA-SEPARATED)", font=self.f_badge, bg=BG, fg=MUTED).pack(pady=(26, 6))
        guild = tk.Entry(c, font=self.f_body, bg=CARD, fg=FG, insertbackground=FG,
                         relief="flat", justify="center", width=30)
        known = {gid: name for name, gid in self.guilds()}
        guild.insert(0, ", ".join(known))
        guild.pack(ipady=6)
        tk.Label(c, text="Discord → Settings → Advanced → Developer Mode,\n"
                         "then right-click the server icon → Copy Server ID.\n"
                         "Leave empty to block Discord completely.",
                 font=self.f_small, bg=BG, fg=MUTED, justify="center").pack(pady=(8, 0))

        tk.Label(c, text="EXTRA BLOCKED SITES  (ONE PER LINE)", font=self.f_badge,
                 bg=BG, fg=MUTED).pack(pady=(24, 6))
        sites = tk.Text(c, height=5, width=36, font=self.f_small, bg=CARD, fg=FG,
                        insertbackground=FG, relief="flat", padx=10, pady=8)
        sites.pack()

        tk.Label(c, text="EXTRA BLOCKED APPS  (ONE .EXE PER LINE)", font=self.f_badge,
                 bg=BG, fg=MUTED).pack(pady=(18, 6))
        apps = tk.Text(c, height=4, width=36, font=self.f_small, bg=CARD, fg=FG,
                       insertbackground=FG, relief="flat", padx=10, pady=8)
        apps.pack()

        def save():
            ids = [x.strip() for x in guild.get().split(",") if x.strip()]
            if any(not x.isdigit() for x in ids):
                messagebox.showwarning("StudyLock", "Discord server IDs are digits only.")
                return
            cfg.pop("discord_allowed_guild_id", None)
            cfg["discord_allowed_guilds"] = [{"name": known.get(x, "Server"), "id": x} for x in ids]
            for raw, key in ((sites.get("1.0", "end"), "blocked_domains"),
                             (apps.get("1.0", "end"), "blocked_processes")):
                extra = [x.strip().lower().replace("https://", "").replace("http://", "").strip("/")
                         for x in raw.splitlines() if x.strip()]
                if extra:
                    cfg[key] = sorted(set(cfg.get(key, []) + extra))
            save_config(cfg)
            self.setup_view()

        btn = tk.Label(c, text="SAVE", font=self.f_btn, bg=ACCENT, fg="#06121c",
                       width=18, pady=10, cursor="hand2")
        btn.pack(pady=(26, 0))
        btn.bind("<Button-1>", lambda e: save())
        back = tk.Label(c, text="back", font=self.f_small, bg=BG, fg=MUTED, cursor="hand2")
        back.pack(pady=12)
        back.bind("<Button-1>", lambda e: self.setup_view())
        self.fit()

    def open_setup(self):
        path = os.path.join(BASE, "SETUP.html")
        try:
            os.startfile(path)
        except OSError:
            messagebox.showinfo("StudyLock", "Setup guide: %s" % path)


def main():
    show_done = "--done" in sys.argv
    root = tk.Tk()
    try:
        root.iconbitmap(default="")
    except tk.TclError:
        pass
    App(root, show_done=show_done)
    root.mainloop()


if __name__ == "__main__":
    main()
