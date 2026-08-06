#!/bin/bash
# Kiosk launcher for Pi Zero 2W / Pi running DietPi + Cog browser
# Rotates the display to portrait and loads the Family Dashboard kiosk.
#
# Install: copy to /home/dietpi/start-kiosk.sh, chmod +x, and run it at boot
# (e.g. via rc.local, cron @reboot, or a systemd unit).
#
# Customise KIOSK_URL to point at your dashboard server.

sleep 1
wlr-randr --output HDMI-A-1 --transform 270
export WEBKIT_CHOICE_MIXED_CONTENT_POLICY=0
export WEBKIT_DISABLE_WEB_SECURITY=1

# Clear WPE WebKit cache to ensure fresh content
rm -rf /home/dietpi/.cache/wpe /home/dietpi/.local/share/wpe

KIOSK_URL="http://192.168.102.230:8080/kiosk"

exec /usr/bin/cog "$KIOSK_URL"
