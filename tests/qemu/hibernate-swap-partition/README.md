# S4 from a swap *partition*, in QEMU

`core/hibernate.configure()` has two branches. The swapfile one is exercised
by the laptop this tool is developed on; the partition one was only ever
covered by unit tests that monkeypatch the two facts it turns on, because no
machine here has a swap partition. This rig builds one.

The single claim under test: **a swap partition is named by the UUID
`lsblk -no UUID` reports (the mkswap signature, not a filesystem UUID), and
that UUID still resolves in the initramfs when the kernel goes looking for
the hibernation image.**

## Running it

```bash
cd tests/qemu/hibernate-swap-partition
python3 rig.py install     # live ISO -> partitioned guest with a swap partition
python3 rig.py hibernate   # run archsetup swap-hibernate, then hibernate
python3 rig.py resume      # boot again; report whether the session came back
```

No root on the host: every privileged step happens inside the guest. Artifacts
land in `$XDG_CACHE_HOME/archsetup-qemu/swaptest/` (a 12G qcow2 that fills to
~1.6G, plus the ISO kernel/initramfs and a per-phase serial log). `install`
reuses the ISO `run-vm.sh` downloads; `hibernate` and `resume` are a pair and
must run in that order, because a successful resume consumes the image.

## What the rig asserts, and why each check is there

- `swapon --show=TYPE` says `partition` — the kernel's own word, not ours.
- The current boot carries no resume pointer other than the one archsetup
  wrote. Without that check a resume proves nothing: something else could
  have brought the machine back.
- `configure()` writes `resume=UUID=<lsblk uuid>` and **no** `resume_offset`.
- Hibernation is entered through `echo disk > /sys/power/state`, not
  `systemctl hibernate`. systemd also writes a `HibernateLocation` EFI
  variable, which would give the next boot a second, independent pointer.
- Coming back is judged by three things at once: `boot_id` unchanged, a token
  written to `/dev/shm` (tmpfs, so a cold boot loses it) still readable, and
  the prompt the pre-hibernate session was left at.

## Things that cost time here, so they do not cost it twice

- `-drive media=cdrom` produced **no** `/dev/sr0` under this QEMU; archiso
  then failed its device search and dropped to the rootfs shell. The ISO is
  attached as a read-only virtio disk instead.
- The live ISO has no `git`. The clone runs inside the chroot, where the
  target already has it.
- The serial prompt carries colour escapes *inside* the hostname
  (`root<CSI>@archiso`), so patterns must be matched against an
  ANSI-stripped copy of the stream. Bash's OSC 3008 shell integration wraps
  every reply as well; the rig turns it off with `PROMPT_COMMAND=`.
- archsetup refuses to run as root, so the guest gets an ordinary user with
  NOPASSWD sudo.
- A command piped into `tail` reports `tail`'s exit code. Output goes to a
  file and the real status is re-raised.

## Not measured here

The busybox branch of `_ensure_resume_hook()`. Arch's default
`mkinitcpio.conf` now ships the `systemd` hook, so the guest takes the same
path the laptop does and `configure()` skips the hook. Exercising the other
branch means editing `HOOKS` in the guest before the `hibernate` phase.
