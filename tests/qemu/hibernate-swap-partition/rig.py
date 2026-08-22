#!/usr/bin/env python3
"""Drive a QEMU guest over its serial socket: install, hibernate, resume.

Built for one measurement: does a real S4 round trip come back when resume=
names a swap *partition* by the UUID archsetup reads with `lsblk -no UUID`?
"""
from __future__ import annotations

import base64
import json
import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path

BASE = Path(os.path.expanduser("~/.cache/archsetup-qemu"))
D = BASE / "swaptest"
ISO = BASE / "archlinux-x86_64.iso"
SOCK = D / "serial.sock"
STATE = D / "state.json"
OVMF_CODE = "/usr/share/edk2/x64/OVMF_CODE.4m.fd"
ARCHISO_UUID = "2026-07-01-16-36-20-00"
HERE = Path(__file__).resolve().parent


def qemu_args(ram: int, iso_boot: bool) -> list[str]:
    args = [
        "qemu-system-x86_64",
        "-enable-kvm", "-cpu", "host", "-smp", "4", "-m", str(ram),
        "-drive", f"if=pflash,format=raw,readonly=on,file={OVMF_CODE}",
        "-drive", f"if=pflash,format=raw,file={D/'OVMF_VARS.fd'}",
        "-drive", f"file={D/'disk.qcow2'},if=virtio,format=qcow2",
        "-nic", "user,model=virtio-net-pci",
        "-display", "none", "-monitor", "none",
        "-serial", f"unix:{SOCK},server=on,wait=off",
    ]
    if iso_boot:
        args += [
            "-kernel", str(D / "vmlinuz-linux"),
            "-initrd", str(D / "initramfs-linux.img"),
            "-append", f"archisobasedir=arch archisosearchuuid={ARCHISO_UUID} "
                       "console=ttyS0,115200",
            "-drive", f"file={ISO},if=virtio,format=raw,readonly=on",
        ]
    return args


# Terminal escapes land in the middle of the prompt text ("root<CSI>@archiso"),
# so every pattern would have to anticipate them. Strip them from the matching
# buffer instead; the log file keeps the raw stream.
ANSI = re.compile(
    rb"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"   # OSC
    rb"|\x1b\[[0-?]*[ -/]*[@-~]"                # CSI
    rb"|\x1b[()][A-Za-z0-9]"                    # charset select
    rb"|\x1b[=>78MP]"                           # single-char escapes
    rb"|[\x00\x07]"
)


def strip_ansi(data: bytes) -> bytes:
    return ANSI.sub(b"", data)


class Guest:
    def __init__(self, ram: int, iso_boot: bool, log: Path):
        if SOCK.exists():
            SOCK.unlink()
        self.logf = open(log, "wb")
        self.proc = subprocess.Popen(
            qemu_args(ram, iso_boot),
            stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT,
        )
        self.buf = b""
        self.carry = b""
        self.n = 0
        self.s = None
        for _ in range(200):
            if self.proc.poll() is not None:
                raise RuntimeError("QEMU açılır açılmaz öldü")
            try:
                s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                s.connect(str(SOCK))
                self.s = s
                break
            except OSError:
                time.sleep(0.1)
        if self.s is None:
            raise RuntimeError("serial soketine bağlanılamadı")

    def _pump(self, timeout: float) -> bytes:
        """Raw bytes off the wire, logged verbatim, returned ANSI-stripped.

        An escape sequence can straddle a recv() boundary, so anything after
        the last ESC is held back until the rest of it arrives.
        """
        self.s.settimeout(timeout)
        try:
            raw = self.s.recv(65536)
        except (socket.timeout, OSError):
            return b""
        if not raw:
            return b""
        self.logf.write(raw)
        self.logf.flush()
        data = strip_ansi(self.carry + raw)
        cut = data.rfind(b"\x1b")
        if cut != -1 and len(data) - cut < 4096:
            self.carry, data = data[cut:], data[:cut]
        else:
            self.carry = b""
        return data

    def expect(self, pattern: str, timeout: float = 300):
        rx = re.compile(pattern.encode())
        end = time.time() + timeout
        while True:
            m = rx.search(self.buf)
            if m:
                out = self.buf[: m.end()]
                self.buf = self.buf[m.end():]
                return m, out.decode(errors="replace")
            if time.time() > end:
                raise TimeoutError(
                    f"beklenen görülmedi: {pattern!r}\n--- son 3000 bayt ---\n"
                    + self.buf[-3000:].decode(errors="replace")
                )
            chunk = self._pump(1.0)
            if chunk:
                self.buf += chunk
            elif self.proc.poll() is not None:
                raise RuntimeError(f"QEMU sonlandı; beklenen yok: {pattern!r}")

    def send(self, line: str) -> None:
        self.s.sendall(line.encode() + b"\n")
        time.sleep(0.05)

    def run(self, cmd: str, timeout: float = 300):
        self.n += 1
        n = self.n
        # The marker is split in the command text so the shell's echo of the
        # line itself cannot match the pattern we are waiting for.
        self.send(f'{cmd}; echo "RIG""DONE{n} rc=$?"')
        m, out = self.expect(rf"RIGDONE{n} rc=(\d+)", timeout)
        return int(m.group(1)), out

    def wait_exit(self, timeout: float = 300) -> int:
        end = time.time() + timeout
        while time.time() < end:
            if self.proc.poll() is not None:
                return self.proc.returncode
            chunk = self._pump(1.0)
            if chunk:
                self.buf += chunk
        self.proc.kill()
        raise TimeoutError("QEMU kapanmadı")

    def kill(self):
        if self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait()


def banner(msg: str) -> None:
    print(f"\n=== {msg} ===", flush=True)


def send_script(g: Guest, path: Path, dest: str) -> None:
    """Push a local script into the guest as base64 over the serial line."""
    b64 = base64.b64encode(path.read_bytes()).decode()
    g.run(f"rm -f {dest}.b64 {dest}", timeout=30)
    for i in range(0, len(b64), 512):
        rc, _ = g.run(f"printf %s '{b64[i:i+512]}' >> {dest}.b64", timeout=30)
        if rc != 0:
            raise RuntimeError("base64 parçası yazılamadı")
    rc, out = g.run(f"base64 -d {dest}.b64 > {dest} && wc -c {dest}", timeout=30)
    if rc != 0:
        raise RuntimeError(f"base64 çözülemedi: {out}")
    print(out.strip()[-200:], flush=True)


def phase_install() -> None:
    banner("FAZ 1 — canlı ISO'dan kurulum")
    g = Guest(ram=4096, iso_boot=True, log=D / "log-install.txt")
    try:
        m, _ = g.expect(r"archiso login:|root@archiso", timeout=300)
        if b"login:" in m.group(0):
            g.send("root")
            g.expect(r"root@archiso", timeout=60)
        time.sleep(2)
        g.send("")
        g.run("stty -echo 2>/dev/null; PROMPT_COMMAND=; PS1='rig> '", timeout=60)
        rc, out = g.run("uname -r; cat /sys/firmware/efi/fw_platform_size", timeout=60)
        print(out.strip()[-300:], flush=True)
        if rc != 0:
            raise RuntimeError("canlı ortam UEFI'de değil")
        send_script(g, HERE / "guest-install.sh", "/root/install.sh")
        banner("kurulum koşuyor (pacstrap dahil)")
        rc, out = g.run("bash /root/install.sh > /root/install.log 2>&1; RC=$?; tail -45 /root/install.log; (exit $RC)", timeout=2400)
        print(out, flush=True)
        if rc != 0 or "RIG_INSTALL_OK" not in out:
            raise RuntimeError("kurulum düştü")
        uuids = dict(re.findall(r"RIG_(\w+)=([0-9a-fA-F-]+)", out))
        STATE.write_text(json.dumps(uuids, indent=2))
        print("kaydedildi:", uuids, flush=True)
        g.send("poweroff")
        g.wait_exit(120)
        banner("FAZ 1 TAMAM")
    finally:
        g.kill()


def phase_hibernate() -> None:
    banner("FAZ 2 — kurulu sistem: swap bölümü + archsetup + hibernate")
    g = Guest(ram=2048, iso_boot=False, log=D / "log-hibernate.txt")
    try:
        m, _ = g.expect(r"swaptest login:|root@swaptest", timeout=300)
        if b"login:" in m.group(0):
            g.send("root")
            g.expect(r"root@swaptest", timeout=60)
        time.sleep(2)
        g.send("")
        g.run("stty -echo 2>/dev/null; PROMPT_COMMAND=; PS1='rig> '", timeout=60)

        banner("2a — takas alanı gerçekten bölüm mü")
        _, out = g.run("swapon --show=NAME,TYPE,SIZE --bytes --noheadings", timeout=60)
        print(out.strip(), flush=True)

        banner("2b — archsetup'ın okuduğu UUID / bu boot'un cmdline'ı")
        _, out = g.run("echo RIG_LSBLK=$(lsblk -no UUID /dev/vda2); "
                       "echo RIG_BLKID=$(blkid -s UUID -o value /dev/vda2); "
                       "echo RIG_CMDLINE=$(cat /proc/cmdline)", timeout=60)
        print(out.strip(), flush=True)
        lsblk_uuid = re.search(r"RIG_LSBLK=([0-9a-f-]{36})", out).group(1)
        # A resume= naming our own partition is what a second cycle is
        # supposed to carry; one naming anything else would mean some other
        # pointer could bring the machine back, and the run cannot tell.
        stale = [tok for tok in re.findall(r"resume\S*=\S+", out)
                 if tok not in (f"resume=UUID={lsblk_uuid}",)]
        if stale:
            raise RuntimeError(f"bu boot başka bir resume işaretçisi taşıyor: {stale}")

        banner("2c — sudo'lu normal kullanıcı (araç root'u reddediyor)")
        _, out = g.run("id rig >/dev/null 2>&1 || useradd -m rig; passwd -d rig >/dev/null; "
                       "printf 'rig ALL=(ALL:ALL) NOPASSWD: ALL\n' > /etc/sudoers.d/rig; "
                       "chmod 440 /etc/sudoers.d/rig; id rig", timeout=60)
        print(out.strip(), flush=True)

        banner("2d — archsetup swap-hibernate")
        rc, out = g.run(
            "cd /opt/archsetup && runuser -u rig -- env PYTHONPATH=/opt/archsetup/src "
            "python -m archsetup --lang en swap-hibernate > /tmp/as.log 2>&1; RC=$?; "
            "tail -25 /tmp/as.log; (exit $RC)", timeout=900)
        print(out, flush=True)
        if rc != 0:
            raise RuntimeError(f"swap-hibernate rc={rc}")

        banner("2e — configure() ne yazdı")
        _, out = g.run("grep -n options /boot/loader/entries/arch.conf", timeout=60)
        print(out.strip(), flush=True)
        if f"resume=UUID={lsblk_uuid}" not in out:
            raise RuntimeError("resume=UUID= yazılmamış")
        if "resume_offset" in out:
            raise RuntimeError("bölüm dalına resume_offset yazılmış")

        banner("2f — hibernate öncesi işaretler")
        g.run("head -c 16 /dev/urandom | base64 > /dev/shm/rig-token", timeout=60)
        _, out = g.run("echo RIG_BOOTID=$(cat /proc/sys/kernel/random/boot_id); "
                       "echo RIG_TOKEN=$(cat /dev/shm/rig-token); "
                       "echo RIG_UP=$(cut -d' ' -f1 /proc/uptime)", timeout=60)
        print(out.strip(), flush=True)
        state = {"lsblk_uuid": lsblk_uuid,
                 "boot_id": re.search(r"RIG_BOOTID=([0-9a-f-]{36})", out).group(1),
                 "token": re.search(r"RIG_TOKEN=(\S+)", out).group(1)}
        STATE.write_text(json.dumps(state, indent=2))
        print("kaydedildi:", state, flush=True)

        banner("2g — çekirdeğin kendi arayüzü: echo disk > /sys/power/state")
        # Not systemctl: systemd would also write a HibernateLocation EFI
        # variable, giving the next boot a second pointer and making it
        # impossible to say the cmdline archsetup wrote is what brought the
        # machine back.
        g.send("sync; echo disk > /sys/power/state")
        rc = g.wait_exit(900)
        print(f"QEMU çıkışı: {rc}", flush=True)
        banner("FAZ 2 TAMAM — makine hazırda")
    finally:
        g.kill()


def phase_resume() -> None:
    banner("FAZ 3 — yeniden başlat: resume swap BÖLÜMÜNDEN geldi mi")
    state = json.loads(STATE.read_text())
    g = Guest(ram=2048, iso_boot=False, log=D / "log-resume.txt")
    try:
        # "rig>" is the prompt the pre-hibernate session was left at, so
        # which of these matches is itself the first piece of evidence.
        m, _ = g.expect(r"rig>|swaptest login:|root@swaptest", timeout=420)
        landed = m.group(0).decode()
        print(f"karşılayan istem: {landed!r}", flush=True)
        if b"login:" in m.group(0):
            g.send("root")
            g.expect(r"root@swaptest", timeout=60)
        time.sleep(2)
        g.send("")
        g.run("stty -echo 2>/dev/null; PROMPT_COMMAND=; PS1='rig> '", timeout=60)
        _, out = g.run("echo RIG_BOOTID=$(cat /proc/sys/kernel/random/boot_id); "
                       "echo RIG_TOKEN=$(cat /dev/shm/rig-token 2>&1); "
                       "echo RIG_UP=$(cut -d' ' -f1 /proc/uptime); "
                       "echo RIG_CMDLINE=$(cat /proc/cmdline)", timeout=60)
        print(out.strip(), flush=True)
        got = re.search(r"RIG_BOOTID=([0-9a-f-]{36})", out)
        same_boot = bool(got) and got.group(1) == state["boot_id"]
        token_back = state["token"] in out

        banner("3b — çekirdeğin resume kaydı")
        _, out2 = g.run("journalctl -b -o short-monotonic --no-pager 2>/dev/null | "
                        "grep -iE 'hibernat|resume|PM:' | head -30", timeout=180)
        print(out2, flush=True)

        banner("3c — sonuç")
        print(f"boot_id aynı mı           : {same_boot}  (beklenen {state['boot_id']})",
              flush=True)
        print(f"tmpfs token geri geldi mi : {token_back}", flush=True)
        print(f"karşılayan istem          : {landed!r}", flush=True)
        print("GERÇEK S4 DÖNÜŞÜ (swap bölümü): "
              + ("EVET" if (same_boot and token_back) else "HAYIR"), flush=True)
        g.send("poweroff")
        g.wait_exit(120)
    finally:
        g.kill()


if __name__ == "__main__":
    {"install": phase_install, "hibernate": phase_hibernate,
     "resume": phase_resume}[sys.argv[1]]()
