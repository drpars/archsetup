#!/bin/bash
# archsetup live-ISO bootstrap:
#   curl -L https://raw.githubusercontent.com/drpars/archsetup/main/iso.sh | bash
set -e

if [[ ! -d /run/archiso ]]; then
  echo "Bu betik Arch Linux canlı ISO ortamı içindir." >&2
  exit 1
fi

pacman -Sy --needed --noconfirm git python python-textual
git clone --depth 1 https://github.com/drpars/archsetup /root/archsetup
cd /root/archsetup
exec ./archsetup
