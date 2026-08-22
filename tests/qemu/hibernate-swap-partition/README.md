# S4 from a swap *partition*, in QEMU

`core/hibernate.configure()` has two branches. The swapfile one is exercised
by the laptop this tool is developed on; the partition one was only ever
covered by unit tests that monkeypatch the two facts it turns on, because no
machine here has a swap partition. This rig builds one.

The first claim under test: **a swap partition is named by the UUID
`lsblk -no UUID` reports (the mkswap signature, not a filesystem UUID), and
that UUID still resolves in the initramfs when the kernel goes looking for
the hibernation image.**

The second: **`_ensure_resume_hook()` earns its keep.** It only does anything
on an initramfs without the `systemd` hook, and Arch's default
`mkinitcpio.conf` ships that hook, so nothing here reaches it by accident —
the guest has to be put on the busybox set first.

## Running it

```bash
cd tests/qemu/hibernate-swap-partition
python3 rig.py install                # live ISO -> guest with a swap partition
python3 rig.py hibernate              # archsetup swap-hibernate, then hibernate
python3 rig.py resume                 # boot again; did the session come back?

python3 rig.py hibernate --busybox    # same, on HOOKS without the systemd hook
python3 rig.py hibernate --control    # busybox HOOKS, no resume hook, no archsetup
```

`--busybox` rewrites `HOOKS` to the pre-systemd set and rebuilds the image
itself before archsetup runs, so "the hook was not in the image before" is a
measurement and a re-run says the same thing. `--control` stops there: same
initramfs, same `resume=UUID=` on the cmdline, hook left out, archsetup never
run. Both arms end with `rig.py resume`, which reads the branch out of
`state.json` and inverts the verdict for the control.

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
- **`^` anchors match nothing in a reply.** Bash's OSC 3008 report lands in
  front of the first line, so `^HOOKS=` never fires — and the check that used
  it ("is `resume` already in HOOKS?") returned the right answer for the
  wrong reason, which is the shape a false negative takes. Patterns are
  unanchored and read the last match.
- **A killed guest keeps its ext4 writes and loses its vfat ones.** After one
  crashed run `/etc/mkinitcpio.conf` held the new `HOOKS` while
  `/boot/initramfs-linux.img` was still the old image — conf and image
  disagreeing is exactly what the rig's own baseline rebuild rules out.
- A failed resume leaves the hibernation image on the swap partition, but
  the next boot's `swapon` puts `SWAPSPACE2` back over it (measured: the
  run after the control arm read a clean signature). Phase 2 checks the
  signature anyway, because a leftover image would let the control arm come
  back from somebody else's image and read as a pass.

## What the busybox arm measured (2026-08-22)

- Baseline image, busybox `HOOKS` without `resume`: 322 entries,
  `hooks/resume` 0, `lib/systemd/systemd` 0.
- After `swap-hibernate`: `HOOKS=(... block filesystems resume fsck)` — the
  hook inserted immediately before `fsck` — and the image archsetup rebuilt
  carries `hooks/resume` (329 entries, still no systemd).
- Round trip: `boot_id` unchanged, the `/dev/shm` token readable, the serial
  landing back on the `rig>` prompt the pre-hibernate session was left at.
  Repeated once, same result.
- **Control arm: no return.** Same image minus the hook, same
  `resume=UUID=…` on the kernel command line — fresh boot, new `boot_id`, no
  token, `swaptest login:`. So the cmdline alone does not do it; what brings
  the machine back is the hook `_ensure_resume_hook()` adds. It also settles
  the attribution the other way: `systemd-hibernate-resume.service` carries
  `AssertPathExists=/etc/initrd-release`, so it can only run inside an
  initrd, and there is no systemd in this one.

## Not measured here

Real, non-virtual hardware with a swap partition: whether a virtio block
device and an NVMe partition differ anywhere on the resume path was not
tested.
