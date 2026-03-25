#!/bin/bash
# Run ON the VPS (or via ssh) if nifty_user TCP auth fails but .env has a known password.
# Usage: bash fix_vps_nifty_user_password.sh 'your_password_from_env'
PW="$1"
if [ -z "$PW" ]; then
  echo "Usage: $0 'password'"
  exit 1
fi
sudo -u postgres psql -v ON_ERROR_STOP=1 -c "ALTER ROLE nifty_user WITH LOGIN PASSWORD '$PW';"
