#!/bin/sh
set -eu
Xvfb :99 -screen 0 1280x800x24 &
sleep 1
exec chromium --no-sandbox --disable-dev-shm-usage --remote-debugging-address=127.0.0.1 --remote-debugging-port=9222 --remote-allow-origins=http://localhost --user-data-dir=/home/browser/profile --no-first-run about:blank
