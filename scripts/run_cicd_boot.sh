#!/usr/bin/env bash
# Run once at boot (via @reboot cron) to catch up on any commits that landed
# while the Pi was off. Bypasses CICD_INTERVAL_MINUTES; still only deploys
# if origin/<branch> is ahead of HEAD.
set -u

# If this project deploys via Docker, wait for the daemon — @reboot can fire
# before dockerd has finished starting.
if command -v docker >/dev/null 2>&1; then
    for _ in $(seq 1 30); do
        docker info >/dev/null 2>&1 && break
        sleep 2
    done
fi

ENVIRONMENT=production /usr/bin/python3 "$(dirname "$0")/cicd_update.py" --ignore-interval
