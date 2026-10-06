# Security policy

## Supported versions

Security fixes go into the latest release.

## Reporting a problem

Please report security problems privately through GitHub: the repository's
**Security** tab → **Report a vulnerability**. Don't open a public issue. Once a
fix is out, the report is credited, unless you'd rather not be named.

## What is worth knowing

Keynone runs with your normal user rights and needs some sensitive abilities to
do its job:

* **A low-level keyboard hook** (the listener) to see the dictation hotkey. Keys
  are handled in memory only, never logged or stored.
* **The clipboard and synthetic key presses** (the engine) to insert text. Its own
  clipboard content is marked private, so Windows leaves it out of the clipboard
  history and cloud sync, and the previous clipboard is restored afterwards.
* **UI Automation** to read a few characters around the cursor (smart spacing),
  the selected text (speak-to-edit), and whether a field is a password field
  (password fields never receive text).
* **A local language model server** (llama.cpp), bound to 127.0.0.1 with a random
  port and API key, and ended together with the engine.
* **Downloads** happen only during setup. Every file is fetched over HTTPS and
  checked against a SHA-256 digest pinned in `src/keynone/assets.py`.
* **Messages between Keynone's own processes**: the listener's hidden tray window
  accepts "reload the settings" and "ignore the hotkeys for at most 60 seconds"
  from other local processes. Neither carries data.

Reports about any of these are especially welcome.
