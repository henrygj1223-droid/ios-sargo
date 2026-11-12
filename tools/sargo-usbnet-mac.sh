#!/bin/sh
# macOS: give the phone's USB Ethernet (kernel sweetusb=2) its host address.
#   tools/sargo-usbnet-mac.sh
# Waits for the interface whose MAC is 02:00:5e:77:ee:01, then sets
# 192.168.7.1/24 on it (sudo; the phone is 192.168.7.2). The log itself is
# received by tools/sargo-usblog-mac.py.
mac=02:00:5e:77:ee:01
echo "waiting for the phone's USB Ethernet ($mac)..."
while :; do
	ifname=$(ifconfig -a | awk -v m="$mac" '/^[a-z]/ {n=$1; sub(":$", "", n)} tolower($0) ~ "ether " m {print n; exit}')
	[ -n "$ifname" ] && break
	sleep 1
done
echo "phone is $ifname; setting 192.168.7.1 (enter your Mac password if asked)"
sudo ifconfig "$ifname" inet 192.168.7.1 netmask 255.255.255.0 up && ifconfig "$ifname" | grep -E 'inet |status' && ping -c 3 192.168.7.2
