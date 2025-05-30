# e‑Consul Auto‑Booking Bot (PyAutoGUI + OpenCV)

> **Purpose** – automatically log in to the Ukrainian e‑Consul portal for a list of
> users, search for an available appointment slot that matches each user’s
> constraints and book it, fully emulating real mouse/keyboard activity so that
> Cloudflare can’t block the automation.

---

## 1  Project layout

```
project/
│  manager.py              ← entry‑point (python manager.py)
│  settings.yaml           ← global paths / ports / template profile
│  Tests.py                ← unit‑tests (pytest or `python Tests.py`)
│
├─ assets/                 ← PNG templates for GUI matching (100 % zoom)
├─ users_cfg/              ← one *.yaml per user (see sample below)
├─ keys/                   ← electronic key files (if relative paths are used)
│
├─ core/
│   ├─ gui_driver.py       ← low‑level PyAutoGUI + OpenCV helper
│   ├─ slot_finder.py      ← business logic (4‑step wizard + calendar)
│   └─ specialized_hooks.py← «next_user», «slot_found», …
│
├─ io/
│   ├─ yaml_loader.py      ← parse/validate user YAML
│   ├─ config_watcher.py   ← watchdog thread (hot‑reload)
│   ├─ html_logger.py      ← rich HTML session log
│   └─ ...
│
├─ server/
│   └─ tcp_server.py       ← pause / resume / stop control
└─ utils/
    ├─ logger.py           ← rotating file + console
    ├─ crypto_utils.py     ← Fernet encrypt/decrypt helper
    └─ profile_manager.py  ← temp Chrome profile handling
```

---

## 2  Prerequisites

* **Windows 10/11** (or Linux with X11) 1920×1080 display
* Python ≥ 3.11 (64‑bit)
* Google Chrome installed (stable channel)

### Python packages

```bash
pip install -r requirements.txt
```

`requirements.txt` example:

```
pyautogui
opencv-python
pillow
watchdog
cryptography
pyyaml
```

*(PyAutoGUI pulls PyScreeze + PyGetWindow; Pillow is needed for screenshots)*

---

## 3  First‑time setup

1. **Prepare template Chrome profile**
   *Run once manually:*

   ```bash
   chrome.exe --user-data-dir="chrome_template/profile" --start-fullscreen
   ```

   * Set zoom to 90 % so all form fields fit on screen.
   * Disable first‑run pop‑ups, updates, “welcome” tabs.
   * Close Chrome – the directory becomes the read‑only template.

2. **Generate Fernet key**

   ```bash
   python -m utils.crypto_utils --key   # copy output
   setx FERNET_SECRET_KEY "<generated>"
   ```

3. **Encrypt each user’s key password**

   ```bash
   python -m utils.crypto_utils --encrypt "MyPassword!"
   # → paste resulting token into user YAML as `key_password:`
   ```

4. **Create user YAML** (`users_cfg/alice.yaml`)

   ```yaml
   key_path: "keys/alice.dat"
   key_issuer: "acsk_DFS"
   key_password: "gAAAAABl..."
   birthdate: "1992-05-14"
   gender: "Female"
   country: "Canada"
   consulates: ["Toronto", "Montreal"]
   service: "Оформлення закордонного паспорта"
   client_name:
     surname: "Ivanenko"
     name: "Alisa"
     patronymic: null      # ← means «для себе»
   min_date: "2025-07-01"  # or `days_from_now: 30`
   ```

5. **Collect GUI templates**
   Put full‑resolution PNG screenshots of each button / label referenced in
   `core.slot_finder` into **`assets/`**.

---

## 4  Running the bot

```bash
python manager.py
```

The console will show live logs; an HTML session log appears in
`html_log/session_<timestamp>.html`.

### TCP control

```
# pause processing
echo pause | nc localhost 4567

# resume
echo resume | nc localhost 4567

# graceful shutdown
echo stop   | nc localhost 4567
```

---

## 5  Unit tests

```bash
pytest               # or
python Tests.py -v
```

All tests are self‑contained and require no GUI.

---

## 6  Troubleshooting

| Symptom                       | Possible cause                                                                                              |
| ----------------------------- | ----------------------------------------------------------------------------------------------------------- |
| **Button template not found** | Wrong zoom level / outdated screenshot – recapture PNG at 100 % or adjust `confidence` in `slot_finder.py`. |
| **Chrome not found**          | Add custom path in `_detect_chrome()` inside `core.gui_driver`.                                             |
| **Fernet decrypt error**      | `FERNET_SECRET_KEY` environment variable not set or mismatched with the token.                              |
| **Cloudflare CAPTCHA**        | Manual intervention or integrate a CAPTCHA‑solving service (not included).                                  |

---

© 2025 — MIT License
