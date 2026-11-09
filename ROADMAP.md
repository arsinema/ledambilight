# Roadmap and ideas

This is a living list of ideas that would make LED Ambilight more useful for
everyone: end users, people wiring their first LED strip, and contributors.
Nothing here is a promise; the order is a suggestion.

Priorities are chosen by **impact for the most users / effort**:

| # | Idea | Why it helps | Effort |
|---|---|---|---|
| 1 | One-click support bundle ✅ 1.3.0 | Fixes “it doesn’t work” reports in minutes instead of days | S–M |
| 2 | Window / region capture ✅ 1.4.0 | Privacy, windowed video, games, multi-monitor setups | M |
| 3 | English UI (localization) ✅ 1.4.0 | Opens the project to the international audience | L |
| 4 | In-app updater | Non-technical users can update without searching GitHub | M |
| 5 | Shareable presets ✅ 1.3.0 | One file with scenes, calibration and mapping; easy to share | S–M |
| 6 | Multi-monitor / multi-strip | One strip per monitor edge, or several WLED devices | L |
| 7 | Phone-camera calibration | Point a phone at the strip; no manual gain/offset guessing | L |
| 8 | Home Assistant MQTT Discovery ✅ 1.3.0 | Lights appear in HA automatically, no manual YAML | S–M |
| 9 | Installer + uninstaller ✅ 1.4.0 | Non-technical install, Start Menu shortcut, clean removal | M |

## 1. One-click support bundle

**Problem:** bug reports usually contain “не работает” and nothing else. Logs,
config and mapping are spread across `%LOCALAPPDATA%`, the app folder and the
monitor.

**Idea:** a button in the «Система» tab — **«Собрать отчёт»**. It produces a ZIP:

- `app.log` and its rotations;
- `config.json` with IP/MAC/user paths redacted;
- the current mapping JSON;
- the text of the diagnostics dialog;
- versions of Windows, Python and the app, GPU name, screen size.

Before saving, show what will be included and let the user uncheck files. No
network upload: the user attaches the ZIP to an issue themselves.

**Effort:** S–M. **Impact:** highest for maintainers and for users waiting for
a fix.

## 2. Window / region capture

**Problem:** the app captures the whole monitor. That is wrong for windowed
video, for games on one monitor, and for privacy when the second monitor shows
private content.

**Idea:** a capture-source selector:

- full monitor (current behaviour);
- a selected window (Windows Graphics Capture can capture a single HWND);
- a custom rectangle on the monitor.

Remember the source in the config and fall back to full-screen if the window
disappears. Show a small border around the selected region while configuring.

**Effort:** M. **Impact:** every multi-monitor and windowed-video user.

## 3. English UI / localization

**Problem:** the README has an English section, but every button and dialog is
in Russian. That filters out most of GitHub.

**Idea:**

- move user-visible strings into `locales/ru.json` and `locales/en.json`;
- load them through a small `tr()` helper or Qt `QTranslator`;
- language switch in «Система» with “system language” as default;
- keep Russian as the reference language; English is the fallback.

Community members can then add German, Spanish, Polish, etc. without touching
the logic.

**Effort:** L (many strings), but it can be done tab by tab. **Impact:** the
largest reach increase for the project.

## 4. In-app updater

**Problem:** the app checks GitHub Releases and opens the page, but the user
still has to download the EXE, close the app and replace the file manually.

**Idea:** after “Доступна версия X”:

1. download the release asset to `%TEMP%`;
2. verify SHA-256 against the GitHub asset digest;
3. start a tiny updater script that waits for the app to exit, replaces the EXE
   and relaunches it;
4. keep the previous EXE as `.old` for rollback.

Portable ZIP users get a link instead. Code signing would make SmartScreen
quieter, but is a separate (paid) step.

**Effort:** M. **Impact:** non-technical users.

## 5. Shareable presets

**Problem:** scenes, calibration and strip mapping live in different places and
are awkward to move between PCs.

**Idea:** a single `.ledpreset` file that contains:

- scene presets (brightness, smoothing, audio mode);
- calibration gains/offsets and gamma;
- filters and power limit;
- optionally the strip mapping (with screen size metadata).

Import shows a preview (“Кино, 65% brightness, 90 LEDs, mapping 1920×1080”) and
lets the user choose what to apply. A gallery of community presets can live in
the repository later.

**Effort:** S–M. **Impact:** everyone who owns more than one PC or shares setups.

## 6. Multi-monitor / multi-strip

**Problem:** one instance currently drives one logical strip. Enthusiasts have a
strip per monitor edge, or a strip behind the desk and another behind the TV.

**Idea:**

- capture each monitor separately;
- map each WLED device to its own monitor/zone;
- separate brightness/calibration per strip;
- one “master” brightness that scales all strips.

Needs a config schema migration and a device list in the UI, but no protocol
changes.

**Effort:** L. **Impact:** high-end setups and sim-racing/streaming users.

## 7. Phone-camera calibration

**Problem:** calibration is manual: the user guesses R/G/B gain and offset until
white looks white.

**Idea:** the built-in phone remote gets a “Calibration” page:

1. the app shows a red/green/blue/white test pattern;
2. the phone camera photographs the strip;
3. the app compares expected vs measured colours and suggests gain/offset;
4. optionally detects LED positions on the monitor for the mapping wizard.

Privacy: photos are processed locally in the browser or sent only to the PC on
the same LAN, never to a cloud.

**Effort:** L. **Impact:** “wow” feature and much better colours out of the box.

## 8. Home Assistant MQTT Discovery

**Problem:** MQTT works, but the user has to write entities manually.

**Idea:** publish retained discovery topics for:

- a light entity (on/off, brightness, colour);
- a select entity for scenes;
- sensors for FPS, estimated current and connection state;
- buttons for “start capture”, “blackout”, “diagnostics”.

The existing MQTT bridge already has the state; this is mostly topic templates.

**Effort:** S–M. **Impact:** smart-home users.

## 9. Installer + uninstaller

**Problem:** the release is a portable EXE/ZIP. That is great for enthusiasts,
but “download, put it somewhere, make a shortcut yourself” loses non-technical
users, and SmartScreen still warns about an unsigned file.

**Idea:** an Inno Setup or NSIS installer:

- installs to `%LOCALAPPDATA%\LedAmbilight` (no admin rights needed);
- creates a Start Menu shortcut and optional autostart entry;
- keeps the config in `%APPDATA%` on upgrade;
- registers an uninstaller that removes the program but keeps the user config
  unless explicitly asked.

Code signing is a separate, paid step; it would reduce SmartScreen friction but
is not required for the installer itself.

**Effort:** M. **Impact:** first-time, non-technical users.

## Good first issues

These are small, self-contained and useful:

- translate one tab into English and add the missing keys to `locales/en.json`;
- add 5–10 community-ready scene presets;
- record a 20-second GIF for the README: full-screen LED placement;
- add a test for `StripMapper` with a custom LED count;
- improve error messages when the WLED board is on a 5 GHz-only network.

## How to help

Open an issue with the idea you want to pick up, or comment on an existing one.
Small pull requests are welcome; `python selftest.py` must stay green.
