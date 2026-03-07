from setuptools import setup

APP = ["desk_menubar.py"]
DATA_FILES = [(".", ["desk_iconTemplate.png", "desk_iconTemplate@2x.png"])]
OPTIONS = {
    "argv_emulation": False,
    "plist": {
        "CFBundleName": "Desk Controller",
        "CFBundleDisplayName": "Desk Controller",
        "CFBundleIdentifier": "com.linak.deskcontroller",
        "CFBundleVersion": "1.0.0",
        "CFBundleShortVersionString": "1.0.0",
        "CFBundleIconFile": "desk_icon.icns",
        "LSUIElement": True,  # Menu bar app (no dock icon)
    },
    "iconfile": "desk_icon.icns",
    "packages": [
        "rumps",
        "bleak",
        "linak_controller",
        "yaml",
        "objc",
        "AppKit",
        "Foundation",
        "CoreBluetooth",
        "aiohttp",
        "asyncio",
    ],
    "includes": [
        "PyObjCTools",
        "appdirs",
    ],
    "frameworks": [],
}

setup(
    app=APP,
    data_files=DATA_FILES,
    options={"py2app": OPTIONS},
    setup_requires=["py2app"],
)
