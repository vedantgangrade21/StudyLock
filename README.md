# StudyLock

A study timer for Windows that blocks distractions for the length of a session,
with no stop button, no pause and no bypass.

Pick a duration, press start, and until the clock runs out:

- **Apps are closed on sight.** Games, launchers (Steam, Epic, EA, Ubisoft, Riot),
  Minecraft launchers, Discord desktop, screen recorders - matched by executable
  name *and* by install folder, so new installs are covered automatically.
- **Distracting sites get a full-screen overlay** from a Chrome/Edge extension.
  The overlay lives in a closed shadow root and re-attaches itself if removed.
- **Discord is narrowed to your study servers.** Your allowed servers stay open;
  DMs, other servers, discovery and invite links do not. The block page offers
  one-click links to the servers you are allowed in.
- **YouTube is narrowed to revision.** Video pages, search, playlists and channels
  stay open. The home feed, Shorts and `/gaming` do not.
- **An allowlist always wins.** Study apps and sites can never be blocked by
  anything else on the lists.

## Pomodoro

Optional 25/5 mode. During each 5-minute break everything genuinely unblocks -
processes stop being killed, the browser shield stands down, and the DNS layer
lifts - then blocking resumes automatically.

## How it resists being switched off

- No stop, pause or cancel exists anywhere in the app.
- Closing the timer window does nothing; the blocker is a separate process.
- Two blocker processes watch each other and restart whichever one dies.
- The session is stored on disk and resumes at logon, so rebooting does not help.
- The countdown takes the longer of wall-clock and a monotonic timer, so winding
  the system clock forward does nothing.
- Deleting the state file does not help - the running blocker rewrites it.
- Killing the blocker does not unblock the browser: the extension fails closed
  and keeps the shield up for the rest of the session.
- Removing the extension does not help either. It reports in every few seconds,
  and a browser whose shield goes quiet gets closed until the extension is back.
- Rules are frozen into the session at the moment you press start, so editing
  the config mid-session changes nothing.

**The honest limit:** this is a user-level program on your own PC, not a corporate
lockdown. Someone with administrator rights and enough patience can dismantle it.
What it guarantees is that quitting takes real, deliberate, inconvenient effort.

## Layout

| File | Role |
| --- | --- |
| `studylock.pyw` | The UI: duration picker, countdown, settings, blocklist viewer |
| `guard.py` | The enforcement daemon: kills processes, serves session status, owns the clock |
| `config.json` | Blocklists, allowlists, Discord servers, shield settings |
| `extension/` | Chrome/Edge MV3 extension that draws the overlay |
| `harden.ps1` | Optional one-time hardening (elevated scheduled task, hosts layer, no incognito) |
| `unharden.ps1` | Removes the hardening; refuses to run during a session |
| `SETUP.html` | Setup guide |

## Requirements

Windows 10/11, Python 3.11+ (standard library only - no pip packages, nothing to
`pip install`), and Chrome or Edge for the site overlay.

## Install

1. **Download** - [download the ZIP](../../archive/refs/heads/main.zip) and unzip
   it anywhere, or `git clone https://github.com/vedantgangrade21/StudyLock.git`
2. **Run `install.ps1`** - right-click it, Run with PowerShell. No admin needed.
   It finds Python, creates your `config.json` from the template, and puts a
   StudyLock shortcut on your Desktop.
3. **Install the browser extension** - open `chrome://extensions`, turn on
   Developer mode, click *Load unpacked*, and pick the `extension` folder. Open
   *Details* and allow it in Incognito. Do the same in `edge://extensions` if you
   use Edge, otherwise Edge will not stay open during a session.
4. **Add your Discord servers** - in Discord, Settings -> Advanced -> Developer
   Mode, then right-click a server icon -> Copy Server ID. Paste the IDs into the
   app's settings screen. Leave it empty to block Discord completely.
5. **Optional hardening** - right-click `harden.ps1` -> Run as administrator. This
   registers the blocker as an elevated scheduled task that restarts at logon and
   re-checks every minute, enables the hosts-file layer (which covers *every*
   browser, not just the ones with the extension), and disables Incognito, guest
   mode and new browser profiles. `unharden.ps1` reverses it and refuses to run
   during a session.

If you skip `install.ps1`, start the app directly:

```powershell
pythonw.exe studylock.pyw
```

`SETUP.html` is the same guide in a nicer format.

## Configuring

`config.json` is yours and is not tracked by git - it is created from
`config.example.json` the first time the app runs. Edit it between sessions
(never during one; rules are frozen at start):

- `discord_allowed_guilds` - `[{"name": "...", "id": "..."}]`, the servers that
  stay open
- `blocked_processes` / `blocked_path_fragments` - apps closed on sight, by name
  or by install folder
- `blocked_domains` / `restricted_domains` - sites blocked outright, or narrowed
  to certain paths
- `allowed_processes` / `allowed_domains` - the allowlist, which beats everything
- `require_browser_shield` - set `false` if you do not want the blocker closing a
  browser whose extension has gone quiet

## Licence

GNU General Public License v3.0 - see `LICENSE`.

Copyright (C) 2026 vedantgangrade21. This program is free software: you can
redistribute it and/or modify it under the terms of the GPL as published by the
Free Software Foundation, either version 3 of the License, or (at your option)
any later version. It is distributed in the hope that it will be useful, but
WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or
FITNESS FOR A PARTICULAR PURPOSE. Forks and modified versions must stay open
under the same licence.
