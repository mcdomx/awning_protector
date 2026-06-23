# Raspberry Pi Setup — Awning Protector

## 1. Flash the SD Card

### Install Raspberry Pi Imager

Download from **raspberrypi.com/software** (Mac, Windows, Linux).

### Flash

1. Insert your SD card (32GB+ recommended, Class 10 / A1 or faster)
2. Open Raspberry Pi Imager
3. **Choose Device** → your Pi model (e.g. Raspberry Pi 4)
4. **Choose OS** → Raspberry Pi OS (64-bit) — use the full Desktop image, not Lite, since this Pi drives a 5" touch display in kiosk mode
5. **Choose Storage** → your SD card
6. Click **Next**, then **Edit Settings** when prompted

### Customize before flashing

**General tab:**
- Hostname: `awningprotector` (so the Pi resolves as `awningprotector.local` on the LAN — the kiosk page's QR code depends on this)
- Username: `mcdomx` (must match `deploy/awning-protector.service` and `deploy/awning-watchdog.service`)
- Password: set a strong password
- WiFi SSID and password

**Services tab:**
- Enable SSH → Use password authentication

Click **Save** → **Yes** → **Yes** to flash.

---

## 2. First Boot

Insert the SD card, power on the Pi, wait ~60 seconds, then SSH in:

```bash
ssh mcdomx@awningprotector.local
```

If `.local` doesn't resolve, find the Pi's IP from your router and use that instead.

---

## 3. System Prerequisites

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install python3-pip git -y

pip3 install --user pipenv --break-system-packages
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
source ~/.bashrc
```

Verify Python is available:

```bash
python3 --version
```

---

## 4. Clone the Repo

```bash
cd ~
git clone https://github.com/mcdomx/awning_protector.git awning_protector
cd awning_protector
```

---

## 5. Configure Environment

```bash
nano .env
```

See `CLAUDE.md` (or `README.md`) for the full list of supported env vars. At minimum, this project needs its normal runtime config (`WEATHER_URL`, `AWNING_URL`, `APP_PORT`, `ANTHROPIC_API_KEY`, etc.) plus the CI/CD variables below:

```
CICD_DEPLOY_MODE=systemd
CICD_GIT_BRANCH=main
CICD_INTERVAL_MINUTES=15
CICD_SERVICE_NAME=awning-protector
```

---

## 6. Install Dependencies and Start the Service

```bash
PIPENV_VENV_IN_PROJECT=1 pipenv lock && pipenv install --deploy
```

`pipenv lock` regenerates `Pipfile.lock` for the Pi's Python version. `--deploy` then enforces those exact versions.

Install and start both systemd units (the main app and the watchdog):

```bash
sudo cp deploy/awning-protector.service deploy/awning-watchdog.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable awning-protector awning-watchdog
sudo systemctl start awning-protector awning-watchdog

sudo systemctl status awning-protector
sudo systemctl status awning-watchdog
```

Logs via:

```bash
journalctl -u awning-protector -f
journalctl -u awning-watchdog -f
```

---

## 7. Set Up CI/CD (Auto-Deploy on New Commits)

The CI/CD script calls `systemctl restart awning-protector` non-interactively, so the `mcdomx` user needs passwordless sudo permission for that one command.

```bash
sudo visudo -f /etc/sudoers.d/awning-protector
```

Add this line:

```
mcdomx ALL=(ALL) NOPASSWD: /usr/bin/systemctl restart awning-protector
```

Then install the cron jobs:

```bash
crontab -e
```

Add these lines:

```
* * * * * ENVIRONMENT=production /usr/bin/python3 /home/mcdomx/awning_protector/scripts/cicd_update.py
@reboot /home/mcdomx/awning_protector/scripts/run_cicd_boot.sh
```

The first line fires every minute but gates on `CICD_INTERVAL_MINUTES` (default: 15). The `@reboot` line runs once after a reboot, bypassing the interval throttle so any commits that landed while the Pi was off are picked up immediately instead of waiting up to `CICD_INTERVAL_MINUTES` — it still only deploys if `origin/main` is ahead of `HEAD`. Logs go to `logs/cicd.log`.

Note: CI/CD only restarts `awning-protector` (`CICD_SERVICE_NAME`). If a deploy changes `watchdog.py`, restart that service manually: `sudo systemctl restart awning-watchdog`.

**Pause / resume without editing cron:**

```bash
touch .cicd_disabled   # pause
rm .cicd_disabled      # resume
```

**Manual trigger:**

```bash
./scripts/run_cicd.sh
```

---

## 8. Verify

```bash
curl http://localhost:8767/health
```

Once the service is running, open `http://awningprotector.local:8767/kiosk` from any device on the LAN, or directly on the Pi's own display once kiosk boot mode is configured (see follow-up step).

---

## 9. Kiosk Boot Mode

Boots straight to the dashboard on the 5" Touch Display 2 (720×1280 portrait) instead of a normal desktop.

### 9.1 Auto-login to the desktop

```bash
sudo raspi-config nonint do_boot_behaviour B4
```

`B4` = "Desktop Autologin" — boots straight into the desktop session as `mcdomx` with no login prompt.

### 9.2 Disable screen blanking

```bash
sudo raspi-config nonint do_blanking 1
```

Without this the display powers down after inactivity — fine for a desktop, not for a kiosk that's only ever touched, never moved with a mouse.

### 9.3 Rotate the display to portrait

Bookworm's default compositor (`labwc`, Wayland) doesn't honor the old `lcd_rotate` boot parameter. Easiest one-time setup, since this also rotates touch input to match (the headless `cmdline.txt`/`config.txt` route does not):

1. Enable VNC so you can drive the GUI remotely (no monitor/mouse needed): `sudo raspi-config nonint do_vnc 0`
2. Connect with a VNC viewer to `awningprotector.local`.
3. Open **Preferences → Screen Configuration**, right-click the `DSI-1` output → **Orientation** → portrait. Apply.
4. This persists across reboots and rotates touch coordinates along with the display. You can disable VNC again afterwards (`sudo raspi-config nonint do_vnc 1`) — it's not needed for normal operation.

If you'd rather not enable VNC, the headless alternative is appending `panel_orientation=left_side_up` to the `video=DSI-1:...` mode line in `/boot/firmware/config.txt`, but expect to also recalibrate touch coordinates separately (`vc4-kms-dsi-*` overlay `invx`/`invy` options) — the GUI method avoids that.

### 9.4 Configure the touchscreen device

`~/.config/labwc/rc.xml` needs a `<touch>` entry so labwc maps the touchscreen to the right output and passes real touch events through to apps (Chromium needs genuine touch protocol events, not synthesized mouse clicks, for tap/swipe to work):

```xml
<?xml version="1.0"?>
<openbox_config xmlns="http://openbox.org/3.4/rc">
	<touch deviceName="Goodix Capacitive TouchScreen" mapToOutput="DSI-2" mouseEmulation="no"/>
</openbox_config>
```

Confirm the actual touchscreen device name with `cat /proc/bus/input/devices` and the actual output name with `wlr-randr` if they differ from the above (e.g. a different Touch Display revision). `mouseEmulation` must stay `"no"` (labwc's documented default) — it exists only as a workaround for touch-blind X11/XWayland apps, and forcing it `"yes"` would suppress real touch events for Chromium once it's running natively on Wayland (see 9.5 and the troubleshooting entry below).

### 9.5 Launch the kiosk page on boot

```bash
nano ~/.config/labwc/autostart
```

Append the contents of `deploy/kiosk-autostart` from the repo (don't overwrite the file — it already starts the panel/wallpaper):

```bash
cat /home/mcdomx/awning_protector/deploy/kiosk-autostart >> ~/.config/labwc/autostart
```

It waits for `awning-protector`'s `/health` endpoint before launching Chromium in `--kiosk` mode pointed at `http://localhost:8767/kiosk`, so it doesn't race the systemd service on boot.

Reboot to verify:

```bash
sudo reboot
```

The Pi should come up directly into the kiosk dashboard, rotated to portrait, with no visible cursor (Chromium hides it automatically after a few seconds of touch-only input) and the screen never blanking.

**Exiting kiosk mode for maintenance** — SSH in and `sudo systemctl restart lightdm` won't help since there's no window manager to escape to; instead `pkill chromium` (it won't relaunch until next boot) or just SSH in for everything — the kiosk display doesn't need to be touched to administer the Pi.

---

## Troubleshooting

**pipenv not found in CI/CD** — Check `which pipenv` on the Pi. If cron can't find it, add a `PATH=` line to the crontab (cron doesn't source `~/.bashrc`), e.g.:
```
PATH=/home/mcdomx/.local/bin:/usr/local/bin:/usr/bin:/bin
```

**Watchdog can't reach the app** — `watchdog.py` polls `APP_URL/health` (defaults to `http://localhost:8767`). Confirm `APP_URL` in `.env` matches the port `awning-protector` actually listens on.

**Kiosk QR code shows `localhost`** — Make sure the Pi's mDNS hostname is actually `awningprotector` (`hostname` on the Pi should print `awningprotector`); the kiosk page substitutes `awningprotector.local` for `localhost`/`127.0.0.1` when building the QR URL.

**`chromium: command not found`** — On Bookworm the binary is `chromium`, not the old `chromium-browser` (even if you `apt install chromium-browser`, the RPi-optimized package, the command it installs is still `chromium`). `deploy/kiosk-autostart` already uses the right name; if it's still missing, `sudo apt install chromium-browser`.

**Kiosk boots to a blank/cropped page** — Check the viewport is exactly 720×1280 (`xdotool getdisplaygeometry` or just look for scrollbars); `static/kiosk.css` only drops its decorative padding/frame at that exact resolution. If the display is reporting a different size, the portrait rotation (9.3) didn't take.

**Kiosk shows a connection-refused page after boot and never recovers** — `deploy/kiosk-autostart` waits for `/health` before launching Chromium, but if `awning-protector.service` is unusually slow to start (or crash-looping) the wait could still lose the race or the page could load before the app is fully ready. `pkill chromium` over SSH and it relaunches the wait-loop only on next boot — for now just re-run `chromium --kiosk http://localhost:8767/kiosk` manually, or `sudo reboot` once the service is confirmed healthy.

**Touchscreen works on the desktop but nothing responds inside the kiosk window** — Two things have to both be true for Chromium to receive touch:
1. Chromium must render natively on Wayland, not fall back to XWayland (which has no native touch support). `deploy/kiosk-autostart` passes `--ozone-platform=wayland` for this — confirm it's actually taking effect with `ps aux | grep chromium` (the flag should appear on the renderer/gpu subprocess command lines too, not just the top-level one).
2. `~/.config/labwc/rc.xml`'s `<touch>` entry must have `mouseEmulation="no"` (see 9.4). `mouseEmulation="yes"` makes labwc convert every touch event into a synthetic mouse event compositor-wide — useful for touch-blind XWayland apps, but it means a native-Wayland Chromium never receives real touch events at all, so taps and swipes silently do nothing. If `rc.xml` was set up before the `--ozone-platform=wayland` fix landed, it may still have `mouseEmulation="yes"` as a leftover XWayland-era workaround.

After fixing either, log out/in or `sudo reboot` so labwc re-reads `rc.xml` and Chromium relaunches with the flag.
