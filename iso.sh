#!/bin/bash
# archsetup live-ISO bootstrap:
#   curl -L https://raw.githubusercontent.com/drpars/archsetup/main/iso.sh | bash
set -e

if [[ ! -d /run/archiso ]]; then
  echo "Bu betik Arch Linux canlı ISO ortamı içindir." >&2
  exit 1
fi

pacman -Sy --needed --noconfirm git python python-textual
if [[ -d /root/archsetup/.git ]]; then
  git -C /root/archsetup pull --ff-only
else
  git clone --depth 1 https://github.com/drpars/archsetup /root/archsetup
fi
cd /root/archsetup

# kitty gibi terminallerden ssh ile gelindiğinde terminfo eksik olabilir
if ! infocmp "$TERM" >/dev/null 2>&1; then
  export TERM=xterm-256color
fi

# Bu betik 'curl | bash' ile çalıştığında stdin bir borudur; TUI'nin
# klavyeyi alabilmesi için stdin'i gerçek terminale bağla.
exec ./archsetup </dev/tty
