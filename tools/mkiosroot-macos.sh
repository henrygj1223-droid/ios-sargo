#!/bin/bash
# macOS port of tools/mkiosroot.sh. Builds the same image - one case-sensitive
# journaled HFS+ volume holding the iOS OS root plus its two cryptexes, for the
# userdata partition - with tools macOS ships (hdiutil, newfs_hfs, fsck_hfs)
# instead of apfs-fuse and hfsprogs.
#
#   sudo tools/mkiosroot-macos.sh [--tweaks] OS.dmg SYSTEMOS.dmg APPOS.dmg OUT.img [SIZE]
#
# SIZE uses mkfile syntax (default 16g). --tweaks also runs
# tools/sweet-root-tweaks.py on the mounted volume before it is unmounted.
# Needs root (owners, modes and setuid bits are preserved) and Homebrew rsync 3
# (brew install rsync). Put OUT.img on an internal SSD: copying ~320k small
# files into an image that lives on a USB stick is very slow.
#
# macOS mounts the IPSW's APFS images natively (the OS image is UDIF, the
# cryptexes are raw) and reads their LZBITMAP-compressed files decompressed;
# rsync without -X does not carry the compression xattr, so the copy is
# uncompressed, exactly like the Linux apfs-fuse path. The volume starts at
# byte 0 of OUT.img (no partition map), as the kernel expects in userdata.
set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
tweaks=0
if [[ ${1:-} == --tweaks ]]; then
	tweaks=1
	shift
fi
[[ $# -ge 4 ]] || { sed -n '2,13p' "$0"; exit 2; }
[[ $(id -u) == 0 ]] || { echo "run as root: sudo $0 $*" >&2; exit 1; }
RSYNC=/opt/homebrew/bin/rsync
# No `| head | grep -q` here: under pipefail the early pipe close fails the check.
rsync_version=$("$RSYNC" --version 2>/dev/null || true)
[[ $rsync_version == *"version 3."* ]] ||
	{ echo "need Homebrew rsync 3 at $RSYNC (brew install rsync)" >&2; exit 1; }

os=$(realpath "$1") sysos=$(realpath "$2") appos=$(realpath "$3") out=$4 size=${5:-16g}
[[ ! -e $out ]] || { echo "refusing to overwrite $out" >&2; exit 1; }

work=$(mktemp -d /tmp/mkiosroot.XXXXXX)
attached=""	# devices still attached, newest first (bash 3.2: no empty arrays under set -u)

cleanup() {
	local d
	for d in $attached; do
		hdiutil detach -force "$d" >/dev/null 2>&1 || true
	done
	rm -f "$work/hd.out" "$work/hd.err"
	# rmdir only removes empty (unmounted) directories.
	rmdir "$work"/* "$work" 2>/dev/null || true
}
trap cleanup EXIT

# hdiutil without its deprecation warning; output in $work/hd.out
hd() {
	local rc=0
	hdiutil "$@" >"$work/hd.out" 2>"$work/hd.err" || rc=$?
	grep -v 'is deprecated' "$work/hd.err" >&2 || true
	return $rc
}

# attach IMAGE [hdiutil options...]: sets $dev to the image's whole-disk node
attach() {
	local img=$1
	shift
	hd attach -noverify -noautoopen "$@" "$img"
	dev=$(awk '/^\/dev\/disk/{print $1; exit}' "$work/hd.out")
	[[ -n $dev ]] || { echo "could not attach $img" >&2; exit 1; }
	attached="$dev $attached"
}

detach() {
	hd detach "$1"
	attached=${attached//"$1 "/}
}

mount_ro() {	# IMAGE MOUNTPOINT
	mkdir -p "$2"
	attach "$1" -readonly -nobrowse -owners on -mountpoint "$2"
}

mount_ro "$os" "$work/os"
mount_ro "$sysos" "$work/sysos"
mount_ro "$appos" "$work/appos"

echo "creating $size case-sensitive journaled HFS+ image $out"
mkfile -n "$size" "$out"
attach "$out" -nomount
newfs_hfs -s -J -b 4096 -v iOSRoot "$dev"
detach "$dev"

root=$work/root
mkdir -p "$root"
attach "$out" -nobrowse -owners on -mountpoint "$root"
rootdev=$dev
owners=$(diskutil info "$root" | awk -F: '/Owners/ {gsub(/ /, "", $2); print $2}')
[[ $owners == Enabled ]] ||
	{ echo "file ownership is not enabled on $root (Owners: ${owners:-unknown})" >&2; exit 1; }

copy() {
	local to=${2#"$root"}
	echo "copying $1 -> ${to:-/}"
	mkdir -p "$2"
	"$RSYNC" -aH --numeric-ids \
		--exclude=/.fseventsd --exclude=/.Spotlight-V100 --exclude=/.Trashes \
		"$1/" "$2/"
}
copy "$work/os" "$root"
copy "$work/sysos" "$root/private/preboot/Cryptexes/OS"
copy "$work/appos" "$root/private/preboot/Cryptexes/App"

# Shared cache where dyld looks at ignition level 1: hard links, no extra space.
dsc=private/preboot/Cryptexes/OS/System/Library/Caches/com.apple.dyld
mkdir -p "$root/System/Library/Caches/com.apple.dyld"
for f in "$root/$dsc"/*; do
	ln "$f" "$root/System/Library/Caches/com.apple.dyld/${f##*/}"
done

# dyld does not search the cryptexes at ignition level 1: hard-link every
# cryptex file that has no counterpart in the OS root (libobjc-trampolines,
# libffi-trampolines, libstdc++, ...) to its root path. Symlinks are copied
# (as the Linux script does; BSD ln would link the symlink's target).
for c in OS App; do
	(cd "$root/private/preboot/Cryptexes/$c" && find . ! -type d ! -path './System/Library/Caches/com.apple.dyld/*') |
	while IFS= read -r f; do
		f=${f#./}
		if [[ ! -e $root/$f && ! -L $root/$f ]]; then
			mkdir -p "$root/$(dirname "$f")"
			src="$root/private/preboot/Cryptexes/$c/$f"
			if [[ -L $src ]]; then cp -P "$src" "$root/$f"; else ln "$src" "$root/$f"; fi
			echo "linked $c:/$f"
		fi
	done
done

# logd's log stores, normally made at restore: it checks owner _logd (272)
# and mode, plus a .migrated marker, else asks a helper and crashes
# ("BUG IN LIBTRACE: Failed to migrate directory").
db="$root/private/var/db"
install -d -o 272 -g 272 -m 0751 "$db/diagnostics"
install -d -o 272 -g 272 -m 0755 "$db/uuidtext" "$db/diagnostics/timesync"
for d in diagnostics uuidtext diagnostics/timesync; do
	: >"$db/$d/.migrated"
	chown 272:272 "$db/$d/.migrated"
	chmod 0644 "$db/$d/.migrated"
done

# System keybag for the built-in AppleKeyStore (no passcode).
install -d -m 0700 -o 0 -g 0 "$root/private/var/keybags"
kb="$root/private/var/keybags/systembag.kb"
printf '%s\n' '<?xml version="1.0" encoding="UTF-8"?>' \
	'<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">' \
	'<plist version="1.0"><dict><key>KeyBagKeys</key><data>U1dFRVQ=</data></dict></plist>' >"$kb"
chown 0:0 "$kb"
chmod 0600 "$kb"

if [[ $tweaks == 1 ]]; then
	echo "applying tools/sweet-root-tweaks.py"
	/usr/bin/python3 "$here/sweet-root-tweaks.py" "$root"
fi

df -h "$root"
sync
detach "$rootdev"

# Verify the finished volume.
attach "$out" -nomount
fsck_hfs -f -n "${dev/disk/rdisk}"
detach "$dev"

# The image was created by root; hand it to the user who ran sudo.
[[ -z ${SUDO_UID:-} ]] || chown "$SUDO_UID:${SUDO_GID:-$SUDO_UID}" "$out"
echo "done: $out"
