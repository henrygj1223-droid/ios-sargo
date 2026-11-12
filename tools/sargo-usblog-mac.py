#!/usr/bin/env python3
"""macOS host side of the phone's USB live log (kernel boot-arg sweetusb=2).

    python3 tools/sargo-usblog-mac.py LOGFILE

Waits for the phone's USB Ethernet (CDC-ECM, host-side MAC 02:00:5e:77:ee:01)
to appear, says which interface it is, and appends every UDP datagram the
phone sends to port 5140 to LOGFILE. Giving the interface its address needs
root and is a separate step (the script prints the command):

    sudo ifconfig <if> inet 192.168.7.1 netmask 255.255.255.0 up
"""
import socket
import subprocess
import sys
import time

MAC = '02:00:5e:77:ee:01'
PORT = 5140


def find_interface():
    out = subprocess.run(['ifconfig', '-a'], capture_output=True, text=True).stdout
    name = None
    for line in out.splitlines():
        if line and not line[0].isspace():
            name = line.split(':', 1)[0]
        elif 'ether' in line and MAC in line.lower():
            return name, out
    return None, out


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    log = open(sys.argv[1], 'ab', buffering=0)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(('0.0.0.0', PORT))
    sock.settimeout(1.0)
    print(f'listening on udp/{PORT}; waiting for the phone ({MAC})', flush=True)
    iface, announced, got = None, False, 0
    while True:
        if iface is None:
            iface, _ = find_interface()
            if iface and not announced:
                announced = True
                print(f'phone USB Ethernet is {iface}; run: '
                      f'sudo ifconfig {iface} inet 192.168.7.1 netmask 255.255.255.0 up', flush=True)
        try:
            data, src = sock.recvfrom(65536)
        except socket.timeout:
            continue
        if got == 0:
            print(f'first datagram from {src[0]} at {time.strftime("%H:%M:%S")}', flush=True)
        got += len(data)
        log.write(data)


if __name__ == '__main__':
    main()
