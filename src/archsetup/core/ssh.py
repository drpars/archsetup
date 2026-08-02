"""SSH: sunucu sertleştirme, makineye özel kimlik, agent ve envanter.

Bu modül ~/.ssh'i yönetir ama iki dosyaya bilerek farklı davranır, çünkü
hata maliyetleri eşit değil:

  * ``config.local`` **üretilir**. Yanlış giderse dışarı bağlanamazsınız;
    makinenin başındasınız, düzeltirsiniz.
  * ``authorized_keys`` **asla yeniden yazılmaz**, yalnızca denetlenir.
    Bozuk bir ``from=`` değeri anahtarın hiçbir zaman eşleşmemesine yol açar
    ve içeri bağlanmak imkânsızlaşır; kurtarmak için fiziksel erişim gerekir.

Kişisel veri (LAN alt ağı, host envanteri) depoya girmez: ~/.ssh/archsetup.toml
dosyasından okunur. Dosyanın varlığı aynı zamanda "bu klasör archsetup
tarafından yönetiliyor" işaretidir — biçim sürümü taşıdığı için ileride
düzen değişirse tahmin etmek yerine göç ettirebiliriz.
"""

from __future__ import annotations

import getpass
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from datetime import date
from pathlib import Path

from . import hardware, i18n, services
from .pacman import run
from .prompt import ask_yes
from .sysedit import sudo_write

t = i18n.t

SSH_DIR = Path.home() / ".ssh"
ARCHIVE_DIR = Path.home() / ".ssh-arsiv"
INVENTORY = SSH_DIR / "archsetup.toml"
CONFIG = SSH_DIR / "config"
CONFIG_LOCAL = SSH_DIR / "config.local"
AUTHORIZED_KEYS = SSH_DIR / "authorized_keys"

SSHD_CONFIG = Path("/etc/ssh/sshd_config")
DROPIN_DIR = Path("/etc/ssh/sshd_config.d")
DROPIN = DROPIN_DIR / "10-local.conf"
AGENT_UNIT = "ssh-agent.socket"

INVENTORY_FORMAT = 1
INCLUDE_LINE = "Include ~/.ssh/config.local"

# Kimlik anahtari olan kod barindirma servisleri. Anahtar adi host'un ilk
# etiketinden turer: github.com -> ~/.ssh/github_<makine>.
#
# Birden fazla servise AYNI acik anahtari koymak, iki hesabi anahtar uzerinden
# birbirine baglar: github.com/<kullanici>.keys ve codeberg.org/<kullanici>.keys
# ikisi de HERKESE ACIK uc noktalar (olculdu). Bu yuzden her servisin kendi
# anahtari var ve config.local her birini kendi Host blogunda sabitler.
PRIMARY_FORGE = "github.com"
FORGES = (PRIMARY_FORGE, "codeberg.org")

# Ana sshd_config'te bu anahtarların "no" hâli drop-in'imizle çakışır.
# Include en üstte olduğu ve OpenSSH'ta ilk okunan değer kazandığı için
# drop-in zaten kazanır; yine de kafa karıştırmasın diye yorumlanır.
_CONFLICTING = (
    "PubkeyAuthentication",
    "PasswordAuthentication",
    "KbdInteractiveAuthentication",
    "UsePAM",
)
_CONFLICT_RE = re.compile(
    rf"^[ \t]*({'|'.join(_CONFLICTING)})[ \t]+no[ \t]*$", re.MULTILINE
)

# from="..." icine yazilmadan once dogrulanir: bozuk bir deger sessiz
# kilitlenme demektir.
_CIDR_RE = re.compile(r"^\d{1,3}(\.\d{1,3}){3}/\d{1,2}$")

_KEY_PREFIXES = ("ssh-", "ecdsa-", "sk-")

# from="..." IPv4 yazildiginda, hedefe ADIYLA baglanan bir istemci IPv6
# link-local'e (fe80::...) cozulup reddedilir -- dogru anahtarla, anlasilmaz
# bir hatayla:
#     authorized_keys:N: correct key but not from a permitted host
#       (host=fe80::..., required=192.168.137.0/24)
# Iki cozum var: kisiti IPv6'yi da kapsayacak sekilde genisletmek, ya da
# istemciyi IPv4'e sabitlemek. Ikincisi secildi -- sunucu kisiti dar kaliyor,
# ki asil isi o.
CLIENT_ADDRESS_FAMILY = "inet"

# Gercek bir Include YONERGESI aranir. Duz "config.local" alt dizgi aramasi,
# dosyadaki bir YORUM satirinda gectiginde de eslesir -- bizim yazdigimiz
# baslik yorumu tam olarak o kelimeyi iceriyor -- ve Include satiri
# kaybolmus olsa bile geri eklenmez.
_INCLUDE_RE = re.compile(
    r"^[ \t]*Include\b.*config\.local", re.IGNORECASE | re.MULTILINE
)


# --------------------------------------------------------------------------
# Makine kimliği
# --------------------------------------------------------------------------


def machine_id() -> str:
    """Dosya adında kullanılabilir makine adı (kimlik = hostname).

    Donanım kimlik için kullanılmaz: chassis "ne tür makine" der, "hangi
    makine" demez — iki laptop olsa ikisi de "laptop" derdi ve anahtarlar
    çakışırdı.
    """
    try:
        name = Path("/etc/hostname").read_text(encoding="utf-8").strip()
    except OSError:
        name = ""
    if not name:
        name = subprocess.run(
            ["uname", "-n"], capture_output=True, text=True
        ).stdout.strip()
    return re.sub(r"[^a-z0-9._-]", "-", name.lower()) or "bilinmeyen"


def chassis() -> str:
    out = subprocess.run(["hostnamectl", "chassis"], capture_output=True, text=True)
    return out.stdout.strip() or "unknown"


def board() -> str:
    try:
        return hardware.BOARD_NAME.read_text(encoding="utf-8").strip()
    except OSError:
        return "?"


def forge_key(host: str) -> Path:
    """``~/.ssh/<servis>_<makine>`` — ör. codeberg.org → codeberg_<makine>."""
    return SSH_DIR / f"{host.split('.')[0]}_{machine_id()}"


def github_key() -> Path:
    return forge_key(PRIMARY_FORGE)


def _pub(key: Path) -> Path:
    """Özel anahtarın yanındaki ``.pub`` dosyası.

    ``with_suffix(".pub")`` kullanılamaz: /etc/hostname bir FQDN ise
    (``host.example.com``) makine adı nokta içerir, Path bunu uzantı sayar
    ve ``github_host.example.com`` için ``github_host.example.pub`` üretir —
    var olmayan bir dosya.
    """
    return key.with_name(key.name + ".pub")


def _priv(pub: Path) -> Path:
    return pub.with_name(pub.name[: -len(".pub")])


# --------------------------------------------------------------------------
# Envanter (kişisel veri — depoda değil, ~/.ssh içinde)
# --------------------------------------------------------------------------


def read_inventory() -> dict | None:
    """archsetup.toml'u oku. Yoksa None; bozuksa None + uyarı."""
    if not INVENTORY.is_file():
        return None
    try:
        with open(INVENTORY, "rb") as fh:
            return tomllib.load(fh)
    except (tomllib.TOMLDecodeError, OSError) as exc:
        print(t("ssh.inventory_bad", path=INVENTORY, error=exc))
        return None


def write_marker() -> None:
    """Envanter yoksa iskeletini yaz; varlığı 'bu klasör bizim' demektir."""
    if INVENTORY.exists():
        return
    INVENTORY.write_text(
        f"# ~/.ssh/archsetup.toml — kisisel envanter. Depoya girmez.\n"
        f"format = {INVENTORY_FORMAT}\n"
        f'created = "{date.today().isoformat()}"\n'
        f'created_on = "{machine_id()}"\n'
        "\n"
        "# [lan]\n"
        '# subnet = "192.168.1.0/24"   # authorized_keys from="..." kisiti\n'
        "\n"
        "# [hosts.ornek]\n"
        '# hostname = "192.168.1.82"\n'
        '# user = "kullanici"\n'
        '# key = "ornek_ed25519"\n',
        encoding="utf-8",
    )
    INVENTORY.chmod(0o600)
    print(t("ssh.inventory_created", path=INVENTORY))


def lan_subnet() -> str | None:
    inv = read_inventory() or {}
    subnet = inv.get("lan", {}).get("subnet")
    if subnet and not _CIDR_RE.match(str(subnet)):
        print(t("ssh.subnet_invalid", value=subnet))
        return None
    return subnet


# --------------------------------------------------------------------------
# Anahtar yardımcıları
# --------------------------------------------------------------------------


def has_passphrase(key: Path) -> bool:
    """Boş parolayla açılamıyorsa parolalıdır."""
    return subprocess.run(
        ["ssh-keygen", "-y", "-P", "", "-f", str(key)],
        capture_output=True,
    ).returncode != 0


def fingerprint(pub: Path) -> str:
    out = subprocess.run(
        ["ssh-keygen", "-lf", str(pub)], capture_output=True, text=True
    )
    parts = out.stdout.split()
    return parts[1] if len(parts) > 1 else "?"


def authorized_entries() -> list[tuple[str, str]]:
    """authorized_keys satırlarını (secenekler, yorum) olarak döndür."""
    if not AUTHORIZED_KEYS.is_file():
        return []
    entries = []
    for line in AUTHORIZED_KEYS.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        fields = line.split()
        for index, field in enumerate(fields):
            if field.startswith(("ssh-", "ecdsa-", "sk-")):
                # Yorum alani opsiyoneldir. fields[-1] almak, yorumsuz bir
                # anahtarda base64 govdesini "yorum" diye raporlar ve ciktiyi
                # okunmaz hale getirir. Yorum, tip ve govdeden sonrasidir ve
                # bosluk icerebilir.
                comment = " ".join(fields[index + 2 :])
                entries.append((" ".join(fields[:index]), comment))
                break
    return entries


def authorized_bodies() -> set[str]:
    """authorized_keys'teki anahtar govdeleri.

    Ayni anahtarin ikinci kez eklenmesini gorebilmek icin gerekli: yorum
    ve secenekler degisebilir, anahtarin kendisi degismez.
    """
    if not AUTHORIZED_KEYS.is_file():
        return set()
    bodies = set()
    for line in AUTHORIZED_KEYS.read_text(encoding="utf-8").splitlines():
        fields = line.split()
        for index, field in enumerate(fields):
            if field.startswith(_KEY_PREFIXES) and index + 1 < len(fields):
                bodies.add(fields[index + 1])
                break
    return bodies


def _interactive() -> bool:
    return sys.stdin.isatty()


def _fix_permissions() -> None:
    SSH_DIR.chmod(0o700)
    for item in SSH_DIR.iterdir():
        if item.is_file():
            item.chmod(0o644 if item.suffix == ".pub" else 0o600)


# --------------------------------------------------------------------------
# Görev: durum raporu (salt okunur)
# --------------------------------------------------------------------------


def status() -> int:
    """Hiçbir şeye dokunmadan mevcut durumu raporla."""
    print(t("ssh.status_machine", name=machine_id(), chassis=chassis(), board=board()))

    if not SSH_DIR.is_dir():
        print(t("ssh.no_dir", path=SSH_DIR))
        return 0

    inv = read_inventory()
    if inv is None:
        print(t("ssh.inventory_missing", path=INVENTORY))
    else:
        hosts = inv.get("hosts", {})
        print(
            t(
                "ssh.inventory_ok",
                fmt=inv.get("format", "?"),
                hosts=len(hosts),
                subnet=inv.get("lan", {}).get("subnet", "-"),
            )
        )

    print(t("ssh.status_keys"))
    for pub in sorted(SSH_DIR.glob("*.pub")):
        priv = _priv(pub)
        if not priv.is_file():
            continue
        state = t("ssh.with_pass") if has_passphrase(priv) else t("ssh.no_pass")
        # Genislik en uzun cevirisine gore: "NO PASSPHRASE" 13 karakter.
        print(f"    {priv.name:<24} {state:<14} {fingerprint(pub)}")

    entries = authorized_entries()
    print(t("ssh.status_authorized", count=len(entries)))
    for options, comment in entries:
        note = options or t("ssh.no_restriction")
        print(f"    {comment or '-':<24} {note}")

    print(
        t(
            "ssh.status_server",
            dropin=t("ssh.present") if DROPIN.is_file() else t("ssh.absent"),
            active=_unit_state("sshd"),
        )
    )
    print(
        t(
            "ssh.status_agent",
            state=_unit_state(AGENT_UNIT, user=True),
            loaded=_agent_key_count(),
        )
    )
    return 0


def _unit_state(unit: str, user: bool = False) -> str:
    cmd = ["systemctl"] + (["--user"] if user else []) + ["is-active", unit]
    out = subprocess.run(cmd, capture_output=True, text=True)
    return out.stdout.strip() or "unknown"


def _agent_key_count() -> int:
    """ssh-add SSH_AUTH_SOCK'a bakar, ssh_config'e degil.

    Bu kurulumda degisken bilerek ayarlanmiyor: soketi ~/.ssh/config'teki
    IdentityAgent gosteriyor, boylece kabuk rc dosyalarina dokunmak
    gerekmiyor. O yuzden sayaci dogru okumak icin soket yolunu burada
    tamamliyoruz; yoksa rapor her zaman 0 gosterirdi.
    """
    env = dict(os.environ)
    if "SSH_AUTH_SOCK" not in env:
        runtime = env.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
        socket = Path(runtime) / "ssh-agent.socket"
        if not socket.exists():
            return 0
        env["SSH_AUTH_SOCK"] = str(socket)
    out = subprocess.run(["ssh-add", "-l"], capture_output=True, text=True, env=env)
    if out.returncode != 0:
        return 0
    return len([line for line in out.stdout.splitlines() if line.strip()])


# --------------------------------------------------------------------------
# Görev: makineye özel kimlik + agent
# --------------------------------------------------------------------------


def identity() -> int:
    if not SSH_DIR.is_dir():
        print(t("ssh.no_dir", path=SSH_DIR))
        return 1

    write_marker()
    rc = _ensure_github_key()
    if rc != 0:
        return rc
    _write_config_local()
    _ensure_include()
    _fix_permissions()
    return _enable_agent()


def _ensure_github_key(allow_new: bool = False) -> int:
    key = github_key()
    if key.is_file():
        print(t("ssh.key_present", name=key.name))
        _offer_passphrase(key)
        return 0

    # Başka makinelerin anahtarı varken sessizce yenisini üretmek, GitHub'a
    # eklenmemiş bir anahtarla çalışmaya başlamak demektir (hostname değiştiyse).
    others = [_priv(p).name for p in SSH_DIR.glob("github_*.pub")]
    if others and not allow_new:
        print(t("ssh.identity_conflict", machine=machine_id(), others=", ".join(others)))
        if not ask_yes(t("ssh.identity_conflict_q", name=key.name)):
            print(t("msg.cancelled"))
            return 1

    print(t("ssh.key_generating", name=key.name))
    comment = f"github {machine_id()} ({chassis()}/{board()})"
    if _interactive():
        print(t("ssh.key_passphrase_hint"))
        rc = run(["ssh-keygen", "-t", "ed25519", "-C", comment, "-f", str(key)])
    else:
        rc = run(
            ["ssh-keygen", "-t", "ed25519", "-N", "", "-C", comment, "-f", str(key)]
        )
        print(t("ssh.key_no_tty", path=key))
    if rc != 0:
        return rc
    print(t("ssh.key_add_to_github"))
    print(f"    {_pub(key).read_text(encoding='utf-8').strip()}")
    return 0


def _offer_passphrase(key: Path) -> None:
    """Parolasız bir anahtar için parola eklemeyi teklif et.

    Anahtar diskte durur; parola onu ağa karşı değil, dosyayı ele geçirene
    karşı korur — çalınan laptop, yedeğe düşen kopya, ~/.ssh'i okuyabilen
    bir program.
    """
    if not _interactive() or has_passphrase(key):
        return
    print(t("ssh.key_passphraseless", name=key.name))
    if ask_yes(t("ssh.key_add_passphrase_q")):
        run(["ssh-keygen", "-p", "-f", str(key)])
    else:
        print(t("ssh.key_passphrase_later", path=key))


def _write_config_local() -> None:
    """config.local'i envanterden üret. Elle düzenlenmemesi beklenir."""
    machine = machine_id()
    lines = [
        f"# ~/.ssh/config.local — archsetup tarafindan '{machine}' icin uretildi",
        f"# ({date.today().isoformat()}). Elle duzenlemeyin; yeniden yazilir.",
    ]

    # Birincil servisin blogu kosulsuz yazilir: anahtarini bu gorev uretiyor,
    # yani birazdan var olacak. Digerlerinin anahtari disaridan gelir (her
    # servise ayri anahtar karari), o yuzden yalnizca anahtar diskteyken
    # yazilir -- olmayan bir IdentityFile'i IdentitiesOnly ile sabitlemek,
    # o host'a baglanmayi tumden imkansiz kilardi.
    for host in FORGES:
        key = forge_key(host)
        if host != PRIMARY_FORGE and not key.is_file():
            continue
        lines += [
            "",
            f"Host {host}",
            f"    HostName {host}",
            "    User git",
            f"    IdentityFile ~/.ssh/{key.name}",
            "    IdentitiesOnly yes",
        ]

    inv = read_inventory() or {}
    for name, spec in sorted(inv.get("hosts", {}).items()):
        hostname = spec.get("hostname")
        if not hostname:
            continue
        lines += [
            "",
            f"Host {name}",
            f"    HostName {hostname}",
            f"    AddressFamily {CLIENT_ADDRESS_FAMILY}",
        ]
        if spec.get("user"):
            lines.append(f"    User {spec['user']}")
        if spec.get("key"):
            key_path = SSH_DIR / spec["key"]
            if not key_path.is_file():
                # NOT: i18n.t()'nin ilk parametresi "key" adinda; bicim
                # argumanini "keyfile" olarak adlandirmak zorundayiz.
                print(t("ssh.host_key_missing", host=name, keyfile=spec["key"]))
            lines += [f"    IdentityFile ~/.ssh/{spec['key']}", "    IdentitiesOnly yes"]

    CONFIG_LOCAL.write_text("\n".join(lines) + "\n", encoding="utf-8")
    CONFIG_LOCAL.chmod(0o600)
    print(t("ssh.config_local_written", path=CONFIG_LOCAL))


def _ensure_include() -> None:
    """config.local en üstten dahil edilmeli.

    ssh ilk eşleşen değeri kullanır: özel host'lar önce, ``Host *``
    varsayılanları en sonda olmalı.
    """
    if not CONFIG.is_file():
        CONFIG.write_text(f"{INCLUDE_LINE}\n", encoding="utf-8")
        CONFIG.chmod(0o600)
        print(t("ssh.include_added", path=CONFIG))
        return
    text = CONFIG.read_text(encoding="utf-8")
    if _INCLUDE_RE.search(text):
        return
    CONFIG.write_text(f"{INCLUDE_LINE}\n\n{text}", encoding="utf-8")
    print(t("ssh.include_added", path=CONFIG))


def _enable_agent() -> int:
    """Arch'in hazır ssh-agent.socket unit'i; soket yolu sabit.

    ~/.ssh/config'teki ``IdentityAgent ${XDG_RUNTIME_DIR}/ssh-agent.socket``
    bunu işaret ettiği için kabuk rc dosyalarına SSH_AUTH_SOCK yazmak
    gerekmez — kullanıcının .zshrc'si bir dotfile deposuna symlink olabilir
    ve ona dokunmak istemeyiz.
    """
    if not services.user_unit_exists(AGENT_UNIT):
        print(t("ssh.agent_missing"))
        return 0

    # Zaten calisiyorsa DOKUNMA. services.enable_user_now() sonunda
    # try-restart yapar; bu soketi yeniden baslatir ve agent'taki tum
    # cozulmus anahtarlar duser, kullanici her calistirmada parolalarini
    # yeniden girmek zorunda kalir. Unit'i biz gondermiyoruz (Arch'in
    # openssh paketi gonderiyor), dolayisiyla yeniden yuklemeye de gerek yok.
    if _unit_state(AGENT_UNIT, user=True) == "active":
        print(t("ssh.agent_already"))
        return 0

    rc = run(["systemctl", "--user", "enable", "--now", AGENT_UNIT])
    print(t("ssh.agent_enabled") if rc == 0 else t("ssh.agent_failed"))
    return rc


# --------------------------------------------------------------------------
# Görev: sunucu sertleştirme
# --------------------------------------------------------------------------


def _dropin_content(user: str, password_auth: bool) -> str:
    value = "yes" if password_auth else "no"
    return f"""# {DROPIN} — archsetup tarafindan uretildi ({date.today().isoformat()})
# Makine: {machine_id()} ({chassis()}, {board()})
# Sertlestirme her makinede ayni; chassis'e gore dallanmaya gerek yok.
# Gezici ag riski authorized_keys'teki from="..." kisitiyla karsilanir.
# NOT: Ana sshd_config'teki Include en ustte oldugu ve OpenSSH'ta ilk
# okunan deger kazandigi icin bu dosya ana dosyayi ezer.

PubkeyAuthentication yes
PasswordAuthentication {value}
KbdInteractiveAuthentication {value}
PermitEmptyPasswords no

PermitRootLogin no
AllowUsers {user}

MaxAuthTries 3
MaxSessions 5
LoginGraceTime 30

X11Forwarding no
AllowAgentForwarding no

ClientAliveInterval 300
ClientAliveCountMax 2
"""


def harden() -> int:
    # __main__ root'u zaten reddediyor, ama bu fonksiyon dogrudan da
    # cagrilabilir ve root altinda AllowUsers root + PermitRootLogin no
    # yazmak makineye hicbir kullanicinin giremedigi bir sshd birakir.
    if os.geteuid() == 0:
        print(t("msg.root_forbidden"))
        return 1

    user = getpass.getuser()
    entries = authorized_entries()

    # Kilitlenme koruması: parola girişi kapatılacaksa önce yetkili anahtar olmalı.
    password_auth = False
    if not entries:
        print(t("ssh.lockout_warning"))
        if not ask_yes(t("ssh.lockout_q")):
            print(t("msg.cancelled"))
            return 1
        password_auth = True
    else:
        print(t("ssh.authorized_found", count=len(entries)))

    try:
        original = SSHD_CONFIG.read_text(encoding="utf-8")
    except OSError as exc:
        print(t("ssh.sshd_unreadable", error=exc))
        return 1

    rc = run(["sudo", "mkdir", "-p", str(DROPIN_DIR)])
    if rc != 0:
        return rc
    rc = sudo_write(DROPIN, _dropin_content(user, password_auth))
    if rc != 0:
        return rc

    backup = SSHD_CONFIG.with_name(f"sshd_config.yedek-{date.today().isoformat()}")
    if not backup.exists():
        run(["sudo", "cp", "-a", str(SSHD_CONFIG), str(backup)])
        print(t("ssh.backup_made", path=backup))

    patched = _CONFLICT_RE.sub(lambda m: f"#{m.group(0)}", original)
    if patched != original:
        rc = sudo_write(SSHD_CONFIG, patched)
        if rc != 0:
            return rc

    # Doğrulama başarısızsa sshd'ye hiç dokunma, eski hâle dön.
    if run(["sudo", "sshd", "-t"]) != 0:
        print(t("ssh.validate_failed"))
        run(["sudo", "rm", "-f", str(DROPIN)])
        sudo_write(SSHD_CONFIG, original)
        return 1
    print(t("ssh.validated"))

    rc = run(["sudo", "systemctl", "enable", "--now", "sshd"])
    rc |= run(["sudo", "systemctl", "restart", "sshd"])
    print(t("ssh.sshd_state", state=_unit_state("sshd")))
    _audit_from_restriction()
    return rc


def _audit_from_restriction() -> None:
    """authorized_keys'i denetle ama ASLA yeniden yazma.

    Bozuk bir ``from=`` anahtarın hiçbir zaman eşleşmemesine yol açar ve
    makineye uzaktan erişimi tamamen kapatır. Bu yüzden burada yalnızca
    rapor veriyoruz; düzeltmeyi kullanıcı bilerek yapar.
    """
    subnet = lan_subnet()
    if not subnet:
        return
    for options, comment in authorized_entries():
        if "from=" not in options:
            print(t("ssh.missing_from", comment=comment, subnet=subnet))


# --------------------------------------------------------------------------
# Görev: anahtar yenileme
# --------------------------------------------------------------------------


def rotate() -> int:
    """Kayıp/sızıntı senaryosu: GitHub anahtarını yenile.

    Yalnızca GitHub anahtarını kapsar. LAN anahtarlarını yenilemek karşı
    makinenin authorized_keys'ini de değiştirmeyi gerektirir; oraya
    erişimimiz yok, o yüzden sessizce yarım iş yapmıyoruz.
    """
    key = github_key()
    if not key.is_file():
        print(t("ssh.rotate_nothing", name=key.name))
        return _ensure_github_key(allow_new=True)

    pub = _pub(key)
    old_fp = fingerprint(pub) if pub.is_file() else "?"
    if not ask_yes(t("ssh.rotate_q", name=key.name, fp=old_fp)):
        print(t("msg.cancelled"))
        return 0

    ARCHIVE_DIR.mkdir(mode=0o700, exist_ok=True)
    stamp = date.today().isoformat()
    key.rename(ARCHIVE_DIR / f"{key.name}.{stamp}")
    if pub.is_file():
        pub.rename(ARCHIVE_DIR / f"{pub.name}.{stamp}")
    print(t("ssh.rotate_archived", path=ARCHIVE_DIR))

    rc = _ensure_github_key(allow_new=True)
    if rc == 0:
        print(t("ssh.rotate_remove_old", fp=old_fp))
    return rc


# --------------------------------------------------------------------------
# Görev: yetkili anahtar ekleme (yeniden yazma değil, EKLEME)
# --------------------------------------------------------------------------


def _validate_key(line: str) -> str | None:
    """Yapistirilan satiri ssh-keygen'e dogrulat, parmak izini dondur.

    Gozle "anahtar gibi duruyor" yetmez: e-posta istemcisinden gecerken
    satir kirilmis, bir karakter dusmus ya da base64 bozulmus olabilir.
    Boyle bir satir authorized_keys'e girerse sshd dosyayi okurken o satiri
    atlar -- hata mesaji yok, sadece "neden giremiyorum" var.

    SINIRI: bu yalnizca BICIM denetimi. "Gecerli ama baska bir anahtar"
    durumunu yakalayamaz -- ed25519 anahtarinda ic saglama yoktur, govdenin
    son karakterleri degistirilirse ortaya yine gecerli bir anahtar cikar,
    sadece parmak izi baska olur (olculdu). Tek gercek denetim parmak izini
    kaynak makinedekiyle karsilastirmaktir; gorev bu yuzden eklemeden once
    parmak izini yazdirip onay istiyor.
    """
    with tempfile.NamedTemporaryFile("w", suffix=".pub", delete=False) as fh:
        fh.write(line + "\n")
        temp = Path(fh.name)
    try:
        out = subprocess.run(
            ["ssh-keygen", "-lf", str(temp)], capture_output=True, text=True
        )
    finally:
        temp.unlink(missing_ok=True)
    if out.returncode != 0:
        return None
    parts = out.stdout.split()
    return parts[1] if len(parts) > 1 else None


def _split_key(raw: str) -> tuple[str, str, str] | None:
    """(tur, govde, yorum) — satirda secenek varsa reddeder.

    Yapistirilan satirda zaten ``from=...`` ya da ``command=...`` varsa
    bizimkiyle birlestirmek iki kisitin hangisinin kazandigini belirsiz
    kilar; kullanicinin ne yaptigini bildigi bir satiri da bozmayalim.
    """
    fields = raw.split()
    if not fields or not fields[0].startswith(_KEY_PREFIXES) or len(fields) < 2:
        return None
    return fields[0], fields[1], " ".join(fields[2:])


def authorize() -> int:
    """Baska bir makinenin acik anahtarini authorized_keys'e EKLE.

    Dosya asla yeniden uretilmez (bkz. modul basligi). Ekleme guvenlidir:
    en kotu ihtimalle fazladan bir satir kalir. Kilitlenme riski dosyayi
    yeniden yazmaktan gelir, eklemekten degil.
    """
    if not SSH_DIR.is_dir():
        print(t("ssh.no_dir", path=SSH_DIR))
        return 1
    if not _interactive():
        print(t("ssh.authorize_needs_tty"))
        return 1

    print(t("ssh.authorize_paste"))
    try:
        raw = input("> ").strip()
    except EOFError:
        raw = ""
    if not raw:
        print(t("msg.cancelled"))
        return 1

    parsed = _split_key(raw)
    if parsed is None:
        print(t("ssh.authorize_not_a_key"))
        return 1
    keytype, body, comment = parsed

    fp = _validate_key(f"{keytype} {body} {comment}".strip())
    if fp is None:
        print(t("ssh.authorize_invalid"))
        return 1

    if body in authorized_bodies():
        print(t("ssh.authorize_duplicate", fp=fp))
        return 0

    options = ""
    subnet = lan_subnet()
    if subnet:
        if ask_yes(t("ssh.authorize_from_q", subnet=subnet)):
            options = f'from="{subnet}" '
            print(t("ssh.authorize_ipv6_note", family=CLIENT_ADDRESS_FAMILY))
    else:
        print(t("ssh.authorize_no_subnet"))

    print(t("ssh.authorize_confirm", fp=fp, comment=comment or "-", options=options or "-"))
    print(t("ssh.authorize_compare"))
    if not ask_yes(t("ssh.authorize_q")):
        print(t("msg.cancelled"))
        return 0

    if AUTHORIZED_KEYS.is_file():
        backup = AUTHORIZED_KEYS.with_name(
            f"authorized_keys.yedek-{date.today().isoformat()}"
        )
        if not backup.exists():
            shutil.copy2(AUTHORIZED_KEYS, backup)
            print(t("ssh.backup_made", path=backup))
        text = AUTHORIZED_KEYS.read_text(encoding="utf-8")
        if text and not text.endswith("\n"):
            text += "\n"
    else:
        text = ""

    AUTHORIZED_KEYS.write_text(
        f"{text}{options}{keytype} {body} {comment}".rstrip() + "\n", encoding="utf-8"
    )
    AUTHORIZED_KEYS.chmod(0o600)
    print(t("ssh.authorize_added", path=AUTHORIZED_KEYS, fp=fp))
    return 0


# --------------------------------------------------------------------------
# Görev: bayat known_hosts kaydını silme
# --------------------------------------------------------------------------


def forget() -> int:
    """Sifirlanan bir makinenin known_hosts kaydini sil.

    "REMOTE HOST IDENTIFICATION HAS CHANGED" bir ortadaki-adam kontrolu,
    otomatik susturulacak bir gurultu degil. Bu yuzden gorev her zaman acik
    onay ister ve once o makineyi gercekten sizin sifirladiginizi sorar.
    """
    if not _interactive():
        print(t("ssh.forget_needs_tty"))
        return 1

    inv = read_inventory() or {}
    hosts = sorted(inv.get("hosts", {}))
    if hosts:
        print(t("ssh.forget_hosts", names=", ".join(hosts)))

    try:
        target = input(f"{t('ssh.forget_q')} ").strip()
    except EOFError:
        target = ""
    if not target:
        print(t("msg.cancelled"))
        return 0

    # Envanterdeki ad bir kisayol olabilir; known_hosts gercek adresi tutar.
    spec = inv.get("hosts", {}).get(target, {})
    hostname = spec.get("hostname", target)

    found = subprocess.run(
        ["ssh-keygen", "-F", hostname], capture_output=True, text=True
    )
    if found.returncode != 0 or not found.stdout.strip():
        print(t("ssh.forget_not_found", host=hostname))
        return 0
    print(found.stdout.strip())

    print(t("ssh.forget_warning", host=hostname))
    if not ask_yes(t("ssh.forget_confirm_q", host=hostname)):
        print(t("msg.cancelled"))
        return 0

    rc = run(["ssh-keygen", "-R", hostname])
    if rc == 0:
        print(t("ssh.forget_done", host=hostname))
    return rc
