#!/usr/bin/env python3
"""Linak Desk Controller - macOS Menu Bar App"""

import rumps
import asyncio
import threading
import yaml
import os
import objc
from AppKit import (
    NSApp,
    NSApplicationActivationPolicyRegular,
    NSApplicationActivationPolicyAccessory,
    NSWindow,
    NSWindowStyleMaskTitled,
    NSWindowStyleMaskClosable,
    NSBackingStoreBuffered,
    NSTextField,
    NSButton,
    NSBezelStyleRounded,
    NSFont,
    NSColor,
    NSMakeRect,
    NSWindowController,
)
from Foundation import NSObject
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


class SettingsDelegate(NSObject):
    ref = None

    def save_(self, sender):
        self.ref.do_save()

    def cancel_(self, sender):
        self.ref.do_close()

    def add_(self, sender):
        self.ref.do_add()

    def remove_(self, sender):
        self.ref.do_remove(sender.tag())

    def toggleMac_(self, sender):
        self.ref.do_toggle_mac()


class SettingsWindow:
    def __init__(self, config, on_save):
        self._cfg = dict(config)
        self._cfg["favourites"] = dict(config.get("favourites", {}))
        self._on_save = on_save
        self._fav_rows = []
        self._delegate = SettingsDelegate.alloc().init()
        self._delegate.ref = self
        self._build()

    def show(self):
        NSApp.setActivationPolicy_(NSApplicationActivationPolicyRegular)
        NSApp.activateIgnoringOtherApps_(True)
        self.window.center()
        self.window.makeKeyAndOrderFront_(None)

    def _label(self, text, x, y, w=160, bold=False):
        lbl = NSTextField.alloc().initWithFrame_(NSMakeRect(x, y, w, 20))
        lbl.setStringValue_(text)
        lbl.setBezeled_(False)
        lbl.setDrawsBackground_(False)
        lbl.setEditable_(False)
        lbl.setSelectable_(False)
        if bold:
            lbl.setFont_(NSFont.boldSystemFontOfSize_(13))
        return lbl

    def _field(self, value, x, y, w=200):
        f = NSTextField.alloc().initWithFrame_(NSMakeRect(x, y, w, 24))
        f.setStringValue_(str(value))
        return f

    def _btn(self, title, x, y, w=80, action=None, tag=0):
        b = NSButton.alloc().initWithFrame_(NSMakeRect(x, y, w, 28))
        b.setTitle_(title)
        b.setBezelStyle_(NSBezelStyleRounded)
        b.setTarget_(self._delegate)
        if action:
            b.setAction_(action)
        b.setTag_(tag)
        return b

    def _build(self):
        favs = self._cfg.get("favourites", {})
        W, H = 520, max(480, 360 + len(favs) * 32)
        style = NSWindowStyleMaskTitled | NSWindowStyleMaskClosable
        self.window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, W, H), style, NSBackingStoreBuffered, False
        )
        self.window.setTitle_("Settings")
        cv = self.window.contentView()
        y = H - 40

        # --- General ---
        cv.addSubview_(self._label("MAC Address", 20, y))
        self.mac_field = self._field(self._cfg.get("mac_address", ""), 180, y, 176)
        self.mac_field.setEditable_(False)
        self.mac_field.setTextColor_(NSColor.disabledControlTextColor())
        cv.addSubview_(self.mac_field)
        self.mac_edit_btn = self._btn("Edit", 362, y - 2, 55, action="toggleMac:")
        cv.addSubview_(self.mac_edit_btn)
        y -= 36

        cv.addSubview_(self._label("Connection Timeout (s)", 20, y))
        self.timeout_field = self._field(self._cfg.get("connection_timeout", 10), 180, y, 60)
        cv.addSubview_(self.timeout_field)
        y -= 36

        cv.addSubview_(self._label("Move Period (s)", 20, y))
        self.period_field = self._field(self._cfg.get("move_command_period", 0.4), 180, y, 60)
        cv.addSubview_(self.period_field)
        y -= 44

        # --- Favourites ---
        cv.addSubview_(self._label("Favourites", 20, y, bold=True))
        y -= 28

        self._fav_rows = []
        for name, height in favs.items():
            nf = self._field(name, 30, y, 110)
            hf = self._field(str(height), 150, y, 70)
            ml = self._label("mm", 225, y, 30)
            rb = self._btn("\u2715", 265, y - 2, 30, action="remove:", tag=len(self._fav_rows))
            for v in (nf, hf, ml, rb):
                cv.addSubview_(v)
            self._fav_rows.append((nf, hf, ml, rb))
            y -= 32

        y -= 8
        cv.addSubview_(self._label("Name:", 30, y, 42))
        self.new_name = self._field("", 75, y, 85)
        cv.addSubview_(self.new_name)
        cv.addSubview_(self._label("Height:", 170, y, 50))
        self.new_height = self._field("", 225, y, 60)
        cv.addSubview_(self.new_height)
        cv.addSubview_(self._label("mm", 290, y, 30))
        cv.addSubview_(self._btn("Add", 325, y - 2, 50, action="add:"))

        # --- Buttons ---
        cv.addSubview_(self._btn("Cancel", W - 180, 16, 80, action="cancel:"))
        cv.addSubview_(self._btn("Save", W - 90, 16, 70, action="save:"))

    def do_add(self):
        name = self.new_name.stringValue().strip()
        height = self.new_height.stringValue().strip()
        if not name or not height.isdigit():
            return
        self._snapshot_fields()
        self._cfg["favourites"][name.lower()] = int(height)
        self._rebuild()

    def do_remove(self, tag):
        if 0 <= tag < len(self._fav_rows) and self._fav_rows[tag]:
            name_f = self._fav_rows[tag][0]
            name = name_f.stringValue().strip().lower()
            self._snapshot_fields()
            self._cfg["favourites"].pop(name, None)
            
    def do_toggle_mac(self):
        editable = not self.mac_field.isEditable()
        self.mac_field.setEditable_(editable)
        if editable:
            self.mac_field.setTextColor_(NSColor.controlTextColor())
            self.mac_edit_btn.setTitle_("Lock")
            self.window.makeFirstResponder_(self.mac_field)
        else:
            self.mac_field.setTextColor_(NSColor.disabledControlTextColor())
            self.mac_edit_btn.setTitle_("Edit")

    def do_save(self):
        self._snapshot_fields()
        self._on_save(self._cfg)
        self.window.close()
        NSApp.setActivationPolicy_(NSApplicationActivationPolicyAccessory)

    def do_close(self):
        self.window.close()
        NSApp.setActivationPolicy_(NSApplicationActivationPolicyAccessory)

    def _snapshot_fields(self):
        self._cfg["mac_address"] = self.mac_field.stringValue().strip()
        try:
            self._cfg["connection_timeout"] = int(self.timeout_field.stringValue())
        except ValueError:
            pass
        try:
            self._cfg["move_command_period"] = float(self.period_field.stringValue())
        except ValueError:
            pass
        favs = {}
        for row in self._fav_rows:
            if row is None:
                continue
            n = row[0].stringValue().strip()
            h = row[1].stringValue().strip()
            if n and h.isdigit():
                favs[n.lower()] = int(h)
        self._cfg["favourites"] = favs

    def _rebuild(self):
        self.window.close()
        self._fav_rows = []
        self._build()
        self.show()


class DeskMenuBarApp(rumps.App):
    def __init__(self):
        self.cfg = load_config()
        icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "desk_iconTemplate.png")
        super().__init__("Desk", title=None, icon=icon_path, template=True, quit_button=None)

        # Shared state for thread-safe UI updates
        self._pending_height = None
        self._pending_status = None
        self._current_height = None

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
        self.config_item = rumps.MenuItem("Settings...", callback=self.on_settings)
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
            self._current_height = self._pending_height
            self._pending_height = None
        if self._pending_status is not None:
            s = self._pending_status
            self._pending_status = None
            self.status_item.title = f"Status: {s}"

    # --- Callbacks ---

    def _make_fav_cb(self, height):
        def cb(_):
            self.ctrl.submit(self.ctrl.move_to(height))
        return cb

    def _rebuild_full_menu(self):
        self.menu.clear()
        self.fav_items = {}
        for name, height in self.cfg.get("favourites", {}).items():
            key = f"{name.capitalize()} ({height}mm)"
            item = rumps.MenuItem(key, callback=self._make_fav_cb(height))
            self.fav_items[name] = item
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
        self._rebuild_full_menu()

    def _remove_fav_from_menu(self, name):
        """Remove a favourite from the live menu and config, then save."""
        self.cfg.get("favourites", {}).pop(name, None)
        save_config(self.cfg)
        self._rebuild_full_menu()

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
        if self._current_height is None:
            rumps.alert("Not connected", "Connect to the desk first.")
            self._deactivate()
            return
        current = int(self._current_height)

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
        self._current_height = None

    def on_settings(self, _):
        self._settings_win = SettingsWindow(self.cfg, self._apply_settings)
        self._settings_win.show()

    def _apply_settings(self, new_cfg):
        save_config(new_cfg)
        self.cfg = new_cfg
        self.ctrl.config = dict(new_cfg)
        self._rebuild_full_menu()

    def on_quit(self, _):
        self.ctrl.submit(self.ctrl.disconnect())
        rumps.quit_application()


if __name__ == "__main__":
    DeskMenuBarApp().run()
