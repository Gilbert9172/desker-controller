# desker-controller

Linak 데스크를 macOS 메뉴바에서 제어하는 앱입니다.

## 빌드

### 사전 준비

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install py2app rumps bleak pyyaml pyobjc
```

### 앱 빌드

```bash
python setup.py py2app
```

빌드 결과물은 `dist/Desk Controller.app`에 생성됩니다.

### DMG 생성

```bash
hdiutil create -volname "Desk Controller" -srcfolder dist -ov -format UDZO "Desk Controller.dmg"
```

### 실행

```bash
open "dist/Desk Controller.app"
```
