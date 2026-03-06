#!/usr/bin/env python3
"""Linak Desk Controller - macOS Menu Bar App"""

import rumps
import asyncio
import threading
import yaml
import os
from AppKit import NSApp, NSApplicationActivationPolicyRegular, NSApplicationActivationPolicyAccessory
from bleak import BleakClient
from linak_controller.desk import Desk
from linak_controller.util import Height
from linak_controller.gatt import ReferenceInputService, ReferenceOutputService

CONFIG_DIR = os.path.expanduser("~/Library/Application Support/linak-desk-app")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.yaml")

DEFAULT_CONFIG = {
    "mac_address": "CBC20C3E-828B-698F-87FB-BD6BF24D5C18",
    "base_height": None,
    "adapter_name": "default adapter",
    "scan_timeout": 5,
    "connection_timeout": 10,
    "move_command_period": 0.4,
    "favourites": {
        "sit": 683,
        "stand": 1040,
    },
}


def load_config():
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "r") as f:
            user_cfg = yaml.safe_load(f) or {}
            return {**DEFAULT_CONFIG, **user_cfg}
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(CONFIG_PATH, "w") as f:
        yaml.dump(DEFAULT_CONFIG, f, default_flow_style=False)
    return DEFAULT_CONFIG.copy()


def save_config(cfg):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(CONFIG_PATH, "w") as f:
        yaml.dump(cfg, f, default_flow_style=False)


class DeskController:
    """Manages BLE connection and desk operations in a background asyncio loop."""

    def __init__(self, config):
        self.config = dict(config)
        self.desk = None
        self.loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        self.on_height = None
        self.on_status = None

    def _run_loop(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def submit(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self.loop)

    def _set_status(self, status):
        if self.on_status:
            self.on_status(status)

    def _set_height(self, height_mm):
        if self.on_height:
            self.on_height(height_mm)

    async def connect(self):
        try:
            self._set_status("Connecting...")
            client = BleakClient(self.config["mac_address"])
            await client.connect(timeout=self.config["connection_timeout"])
            self.desk = await Desk.initialise(self.config, client)
            self._set_status("Connected")
            height, _ = await self.desk.get_height_speed()
            self._set_height(height.human)
        except Exception as e:
            self.desk = None
            self._set_status(f"Error: {e}")

    async def disconnect(self):
        if self.desk and self.desk.client.is_connected:
            self.desk.disconnecting = True
            await self.desk.client.disconnect()
        self.desk = None
        self._set_status("Disconnected")

    async def move_to(self, height_mm):
        if not self.desk:
            self._set_status("Not connected")
            return

        target = Height(height_mm, self.config["base_height"], True)
        initial_height, _ = await self.desk.get_height_speed()

        if initial_height.value == target.value:
            self._set_height(initial_height.human)
            return

        self._set_status(f"Moving to {height_mm}mm...")
        await self.desk.wakeup()
        await self.desk.stop()

        data = ReferenceInputService.encode_height(target.value)
        while True:
            await ReferenceInputService.ONE.write(self.desk.client, data)
            await asyncio.sleep(self.config["move_command_period"])
            height, speed = await ReferenceOutputService.get_height_speed(self.desk.client)
            height.base_height = self.config["base_height"]
            self._set_height(height.human)
            if speed.value == 0:
                break

        self._set_status("Connected")

    async def refresh_height(self):
        if not self.desk:
            return
        height, _ = await self.desk.get_height_speed()
        self._set_height(height.human)


class DeskMenuBarApp(rumps.App):
    def __init__(self):
        self.cfg = load_config()
        super().__init__("Desk", title="\u2b0d --mm", quit_button=None)

        # Shared state for thread-safe UI updates
        self._pending_height = None
        self._pending_status = None

        # Menu items
        self.fav_items = {}
        for name, height in self.cfg.get("favourites", {}).items():
            item = rumps.MenuItem(
                f"{name.capitalize()} ({height}mm)",
                callback=self._make_fav_cb(height),
            )
            self.fav_items[name] = item

        self.custom_item = rumps.MenuItem("Move to...", callback=self.on_custom)
        self.add_preset_item = rumps.MenuItem("Add Preset...", callback=self.on_add_preset)
        self.save_current_item = rumps.MenuItem("Save Current Height as...", callback=self.on_save_current)
        self.remove_preset_menu = rumps.MenuItem("Remove Preset")
        self.refresh_item = rumps.MenuItem("Refresh Height", callback=self.on_refresh)
        self.status_item = rumps.MenuItem("Status: Disconnected")
        self.status_item.set_callback(None)
        self.connect_item = rumps.MenuItem("Connect", callback=self.on_connect)
        self.disconnect_item = rumps.MenuItem("Disconnect", callback=self.on_disconnect)
        self.config_item = rumps.MenuItem("Open Config...", callback=self.on_open_config)
        self.quit_item = rumps.MenuItem("Quit", callback=self.on_quit)

        items = list(self.fav_items.values())
        items += [
            None,
            self.custom_item,
            self.refresh_item,
            None,
            self.add_preset_item,
            self.save_current_item,
            self.remove_preset_menu,
            None,
            self.status_item,
            self.connect_item,
            self.disconnect_item,
            None,
            self.config_item,
            None,
            self.quit_item,
        ]
        self.menu = items
        self._rebuild_remove_menu()

        # Controller (background async thread)
        self.ctrl = DeskController(self.cfg)
        self.ctrl.on_height = self._on_height
        self.ctrl.on_status = self._on_status

        # Timer to safely apply UI updates on main thread
        self._ui_timer = rumps.Timer(self._apply_ui_updates, 0.3)
        self._ui_timer.start()

        # Auto-connect
        self.ctrl.submit(self.ctrl.connect())

    # --- Thread-safe UI bridge ---

    def _on_height(self, h):
        self._pending_height = h

    def _on_status(self, s):
        self._pending_status = s

    def _apply_ui_updates(self, _):
        if self._pending_height is not None:
            h = self._pending_height
            self._pending_height = None
            self.title = f"\u2b0d {h:.0f}mm"
        if self._pending_status is not None:
            s = self._pending_status
            self._pending_status = None
            self.status_item.title = f"Status: {s}"

    # --- Callbacks ---

    def _make_fav_cb(self, height):
        def cb(_):
            self.ctrl.submit(self.ctrl.move_to(height))
        return cb

    def _rebuild_remove_menu(self):
        # Remove existing items
        for key in list(self.remove_preset_menu):
            del self.remove_preset_menu[key]
        for name in self.cfg.get("favourites", {}):
            self.remove_preset_menu[name.capitalize()] = rumps.MenuItem(
                name.capitalize(), callback=self._make_remove_cb(name)
            )

    def _add_fav_to_menu(self, name, height):
        """Add a single favourite to the live menu and config, then save."""
        self.cfg.setdefault("favourites", {})[name] = height
        save_config(self.cfg)

        key = f"{name.capitalize()} ({height}mm)"
        item = rumps.MenuItem(key, callback=self._make_fav_cb(height))
        self.fav_items[name] = item
        # Insert before the first separator (index 0 in the internal menu)
        self.menu.insert_before(self.custom_item.title, item)
        self._rebuild_remove_menu()

    def _remove_fav_from_menu(self, name):
        """Remove a favourite from the live menu and config, then save."""
        height = self.cfg.get("favourites", {}).pop(name, None)
        save_config(self.cfg)

        item = self.fav_items.pop(name, None)
        if item:
            del self.menu[item.title]
        self._rebuild_remove_menu()

    def _make_remove_cb(self, name):
        def cb(_):
            self._remove_fav_from_menu(name)
        return cb

    @staticmethod
    def _activate():
        NSApp.setActivationPolicy_(NSApplicationActivationPolicyRegular)
        NSApp.activateIgnoringOtherApps_(True)

    @staticmethod
    def _deactivate():
        NSApp.setActivationPolicy_(NSApplicationActivationPolicyAccessory)

    def on_add_preset(self, _):
        self._activate()
        name_win = rumps.Window(
            message="Preset name (e.g. nap, mid):",
            title="Add Preset",
            default_text="",
            ok="Next",
            cancel="Cancel",
        )
        name_resp = name_win.run()
        if not name_resp.clicked or not name_resp.text.strip():
            self._deactivate()
            return
        preset_name = name_resp.text.strip().lower()

        height_win = rumps.Window(
            message=f"Height for '{preset_name}' in mm:",
            title="Add Preset",
            default_text="",
            ok="Save",
            cancel="Cancel",
        )
        height_resp = height_win.run()
        self._deactivate()
        if not height_resp.clicked or not height_resp.text.strip().isdigit():
            return

        self._add_fav_to_menu(preset_name, int(height_resp.text.strip()))

    def on_save_current(self, _):
        self._activate()
        if self._pending_height is None and self.title == "\u2b0d --mm":
            rumps.alert("Not connected", "Connect to the desk first.")
            self._deactivate()
            return
        try:
            current = int(self.title.replace("\u2b0d ", "").replace("mm", "").strip())
        except ValueError:
            rumps.alert("Error", "Current height is not available.")
            self._deactivate()
            return

        name_win = rumps.Window(
            message=f"Save current height ({current}mm) as:",
            title="Save Preset",
            default_text="",
            ok="Save",
            cancel="Cancel",
        )
        resp = name_win.run()
        self._deactivate()
        if resp.clicked and resp.text.strip():
            self._add_fav_to_menu(resp.text.strip().lower(), current)

    def on_custom(self, _):
        self._activate()
        window = rumps.Window(
            message="Enter target height in mm:",
            title="Move Desk",
            default_text="",
            ok="Move",
            cancel="Cancel",
        )
        resp = window.run()
        self._deactivate()
        if resp.clicked and resp.text.strip().isdigit():
            self.ctrl.submit(self.ctrl.move_to(int(resp.text.strip())))

    def on_refresh(self, _):
        self.ctrl.submit(self.ctrl.refresh_height())

    def on_connect(self, _):
        self.ctrl.submit(self.ctrl.connect())

    def on_disconnect(self, _):
        self.ctrl.submit(self.ctrl.disconnect())
        self.title = "\u2b0d --mm"

    def on_open_config(self, _):
        os.system(f'open "{CONFIG_PATH}"')

    def on_quit(self, _):
        self.ctrl.submit(self.ctrl.disconnect())
        rumps.quit_application()


if __name__ == "__main__":
    DeskMenuBarApp().run()
