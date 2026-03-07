#!/bin/bash
# Create DMG for Desk Controller app

APP_NAME="Desk Controller"
DMG_NAME="DeskController"
DIST_DIR="dist"
DMG_DIR="dmg_staging"
VOLUME_NAME="Desk Controller"
DMG_OUTPUT="${DIST_DIR}/${DMG_NAME}.dmg"

# Clean up previous artifacts
rm -rf "${DMG_DIR}" "${DMG_OUTPUT}"

# Create staging directory with app and Applications symlink
mkdir -p "${DMG_DIR}"
cp -R "${DIST_DIR}/${APP_NAME}.app" "${DMG_DIR}/"
ln -s /Applications "${DMG_DIR}/Applications"

# Create DMG
hdiutil create \
    -volname "${VOLUME_NAME}" \
    -srcfolder "${DMG_DIR}" \
    -ov \
    -format UDZO \
    "${DMG_OUTPUT}"

# Clean up staging
rm -rf "${DMG_DIR}"

echo ""
echo "DMG created: ${DMG_OUTPUT}"
echo "Size: $(du -h "${DMG_OUTPUT}" | cut -f1)"
