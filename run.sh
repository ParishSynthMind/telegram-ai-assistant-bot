#!/bin/bash
# Loads .env and starts the bot.
cd "$(dirname "$0")"
set -a; source .env; set +a
exec python3 bridge.py
