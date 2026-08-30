#!/bin/bash
# archsetup live-ISO bootstrap:
#   curl -L https://raw.githubusercontent.com/drpars/archsetup/main/iso.sh | bash
set -e

if [[ ! -d /run/archiso ]]; then
  echo "Bu betik Arch Linux canlı ISO ortamı içindir." >&2
  exit 1
fi

# glibc listede bilerek duruyor. ISO yaşlanırken depolar ilerliyor, ve
# `pacman -Sy python` bir ay eski ISO'da ISO'nunkinden yeni bir glibc'ye karşı
# derlenmiş python kuruyor: yorumlayıcı tamamen çalışmaz oluyor. Ölçüldü
# (2026-08-30, 2026-07-29 tarihli ISO): ISO glibc 2.43, depo 2.44, ve python
# `ImportError: /usr/lib/libm.so.6: version GLIBC_2.44 not found` veriyordu.
# İkisini aynı işlemde yükseltmek çifti tutarlı tutuyor.
pacman -Sy --needed --noconfirm glibc git python python-textual

# glibc garanti değil: yeni python'un bağlandığı başka bir kütüphane de aynı
# şekilde ilerleyebilir. Kırık yorumlayıcı, TUI'nin içinden gelen bir
# traceback yerine tek bir açık cümleyi hak ediyor.
if ! python -c "import subprocess" >/dev/null 2>&1; then
  echo "HATA: python bu ISO'da çalışmıyor — kütüphaneler kısmi yükseltildi." >&2
  echo "      Bu ISO depolardan eski. Ya güncel bir ISO kullanın, ya da" >&2
  echo "      'pacman -Syu' ile canlı ortamı tümüyle yükseltip tekrar deneyin." >&2
  exit 1
fi
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
