"""Git makine kimliği: ~/.gitconfig.local ve allowed_signers üretimi.

Aynı GitHub hesabına birden fazla makineden push edilirken commit'i hangi
makinenin yaptığını görebilmek için SSH ile commit imzalanır. Kimlik
(``user.name`` / ``user.email``) her makinede **aynıdır** — ayırt edici olan
imzalayan anahtardır. Bu yüzden paylaşılan ``~/.gitconfig`` (dotfiles) yalnız
``[include] path = ~/.gitconfig.local`` taşır ve makineye özel olan tek şey
burada üretilir.

İki dosyaya bilerek farklı davranılır, ``core/ssh.py``'deki ile aynı gerekçe:

  * ``~/.gitconfig.local`` **üretilir**. İçeriği tamamen bu makineye ait,
    yanlış giderse ``git commit`` şikâyet eder ve düzeltirsiniz.
  * ``allowed_signers`` yalnızca **eklenir**, hiçbir satırı silinmez. Bu bir
    *güven listesidir*: başka makinelerin anahtarları elle ya da başka bir
    kurulumdan gelmiş olabilir ve yeniden üretmek onları sessizce düşürür —
    o makinelerin imzaları bir anda "unknown signer" olur.

Anahtar tahmin edilmez: yalnız bu makinenin ``~/.ssh/github_<makine>.pub``
dosyası yazılır. Sahibi doğrulanmamış bir anahtar güven listesine girerse
onunla imzalanan her commit sessizce "geçerli" sayılırdı.
"""

from __future__ import annotations

import shutil
import subprocess
from datetime import date
from pathlib import Path

from . import i18n, ssh
from .prompt import ask_yes

t = i18n.t

GITCONFIG = Path.home() / ".gitconfig"
GITCONFIG_LOCAL = Path.home() / ".gitconfig.local"
ALLOWED_SIGNERS = Path.home() / ".config" / "git" / "allowed_signers"

# Ürettiğimiz dosyayı elle yazılmış olandan ayıran işaret. Üstteki yorum
# satırında durur; olmadığı bir dosyanın üzerine sormadan yazılmaz.
MARKER = "archsetup"

_KEY_PREFIXES = ("ssh-", "ecdsa-", "sk-")


def _git_email() -> str:
    """user.email — hangi dosyadan geldiği önemli değil, etkin değer.

    Üretilmez: allowed_signers'taki e-posta ``user.email`` ile birebir
    eşleşmek zorunda, uydurulmuş bir adres doğrulamayı sessizce bozar.
    """
    out = subprocess.run(
        ["git", "config", "--get", "user.email"], capture_output=True, text=True
    )
    return out.stdout.strip()


def _pub_path() -> Path:
    """github_<makine>.pub — with_suffix kullanilmaz, FQDN'li bir makine
    adinda nokta uzanti sanilir (bkz. core/ssh.py'deki ayni tuzak)."""
    key = ssh.github_key()
    return key.with_name(key.name + ".pub")


def _backup_path(path: Path) -> Path:
    return path.with_name(path.name + ".bak")


def _public_key() -> tuple[str, str] | None:
    """(anahtar-turu, govde) — bu makinenin GitHub açık anahtarı."""
    pub = _pub_path()
    if not pub.is_file():
        return None
    fields = pub.read_text(encoding="utf-8").split()
    if len(fields) < 2 or not fields[0].startswith(_KEY_PREFIXES):
        return None
    return fields[0], fields[1]


def gitconfig_local(machine: str) -> str:
    return f"""# ~/.gitconfig.local — archsetup tarafindan '{machine}' icin uretildi
# ({date.today().isoformat()}). Elle duzenlemeyin; yeniden yazilir.
#
# Kimlik (user.name / user.email) her makinede AYNI kalir; ayirt edici olan
# imzalayan anahtardir.

[gpg]
\tformat = ssh

[gpg "ssh"]
\tallowedSignersFile = {_tilde(ALLOWED_SIGNERS)}

[user]
\tsigningkey = {_tilde(ssh.github_key())}.pub

[commit]
\tgpgsign = true

[tag]
\tgpgsign = true
"""


def _tilde(path: Path) -> str:
    """Ev dizinini ~ ile yaz — git bunu genisletir, okunmasi da kolay."""
    try:
        return f"~/{path.relative_to(Path.home())}"
    except ValueError:
        return str(path)


def signer_bodies(text: str) -> dict[str, str]:
    """allowed_signers metnindeki {anahtar-govdesi: principal} eslemesi.

    Govde anahtarin kendisidir; ayni anahtar farkli e-postayla iki kez
    yazilirsa dogrulama beklenmedik davranir, o yuzden karsilastirma
    e-postaya degil govdeye bakar.
    """
    found = {}
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        fields = line.split()
        for index, field in enumerate(fields):
            if field.startswith(_KEY_PREFIXES) and index + 1 < len(fields):
                found[fields[index + 1]] = fields[0]
                break
    return found


def _write_gitconfig_local(machine: str) -> int:
    if GITCONFIG_LOCAL.is_file():
        first_line = GITCONFIG_LOCAL.read_text(encoding="utf-8").partition("\n")[0]
        # Yedek yalnizca BIZIM olmayan bir dosya icin alinir. Kendi
        # urettigimizin yedegi her calistirmada bir oncekinin kopyasi olurdu:
        # gurultu, ve gercek elle yazilmis surumu de ikinci kosuda ezerdi.
        if MARKER not in first_line:
            print(t("gitid.local_handwritten", path=GITCONFIG_LOCAL))
            if not ask_yes(t("gitid.local_overwrite_q")):
                print(t("msg.cancelled"))
                return 1
            backup = _backup_path(GITCONFIG_LOCAL)
            shutil.copy2(GITCONFIG_LOCAL, backup)
            print(t("gitid.backup", path=backup))

    GITCONFIG_LOCAL.write_text(gitconfig_local(machine), encoding="utf-8")
    print(t("gitid.local_written", path=GITCONFIG_LOCAL))
    return 0


def _merge_allowed_signers(email: str, keytype: str, body: str) -> int:
    ALLOWED_SIGNERS.parent.mkdir(parents=True, exist_ok=True)

    header = (
        "# Git SSH imza dogrulamasi icin guvenilen anahtarlar.\n"
        "# Bicim: <e-posta> <anahtar-turu> <anahtar>\n"
        "# Yalnizca ACIK anahtar icerir; gizli veri yoktur.\n"
        "# archsetup satir EKLER, hicbir satiri silmez.\n"
    )
    text = (
        ALLOWED_SIGNERS.read_text(encoding="utf-8")
        if ALLOWED_SIGNERS.is_file()
        else header
    )

    existing = signer_bodies(text)
    if body in existing:
        if existing[body] != email:
            # Ayni anahtar baska bir e-postayla kayitli. Dokunmuyoruz ama
            # sessiz kalinirsa dogrulama "no principal matched" der ve
            # sebebi gorunmez olur.
            print(t("gitid.signer_email_differs", found=existing[body], want=email))
        else:
            print(t("gitid.signer_present"))
        return 0

    if ALLOWED_SIGNERS.is_file():
        backup = _backup_path(ALLOWED_SIGNERS)
        shutil.copy2(ALLOWED_SIGNERS, backup)
        print(t("gitid.backup", path=backup))

    if not text.endswith("\n"):
        text += "\n"
    ALLOWED_SIGNERS.write_text(f"{text}{email} {keytype} {body}\n", encoding="utf-8")
    print(t("gitid.signer_added", path=ALLOWED_SIGNERS))
    return 0


def _check_include() -> None:
    """Paylasilan ~/.gitconfig .local'i cagiriyor mu?

    Dosyaya DOKUNULMAZ: dotfiles deposuna symlink olabilir ve buradan
    yazmak depoyu kirletir. Git, olmayan bir include'u sessizce yok sayar,
    bu yuzden eksikligi de sessizdir — o yuzden soyluyoruz.
    """
    out = subprocess.run(
        ["git", "config", "--file", str(GITCONFIG), "--get-all", "include.path"],
        capture_output=True,
        text=True,
    )
    if GITCONFIG_LOCAL.name in out.stdout:
        return
    print(t("gitid.include_missing", path=GITCONFIG, local=GITCONFIG_LOCAL.name))


def configure() -> int:
    if shutil.which("git") is None:
        print(t("gitid.git_missing"))
        return 1

    key = _public_key()
    if key is None:
        print(t("gitid.key_missing", path=f"{ssh.github_key()}.pub"))
        return 1

    email = _git_email()
    if not email:
        print(t("gitid.email_missing"))
        return 1
    print(t("gitid.email_used", email=email))

    rc = _write_gitconfig_local(ssh.machine_id())
    if rc != 0:
        return rc

    keytype, body = key
    rc = _merge_allowed_signers(email, keytype, body)
    _check_include()
    if rc == 0:
        print(t("gitid.done"))
    return rc
