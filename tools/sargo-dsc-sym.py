#!/usr/bin/env python3
"""Symbolicate addresses in an iOS 16 split dyld shared cache.

usage: dsc_sym.py CACHE_DIR SLIDE ADDR [ADDR...]
ADDRs are runtime (slid) addresses; SLIDE is the shared cache slide.
"""
import mmap
import os
import struct
import sys
import bisect


def u32(b, o):
    return struct.unpack_from('<I', b, o)[0]


def u64(b, o):
    return struct.unpack_from('<Q', b, o)[0]


class Cache:
    def __init__(self, d):
        self.dir = d
        main = os.path.join(d, 'dyld_shared_cache_arm64')
        self.files = {}
        self.maps = []   # (addr, size, fileoff, mm)
        mm = self._open(main)
        self.main = mm
        self.base = u64(mm, 0xe0)
        self._add_maps(mm)
        sc_off, sc_cnt = u32(mm, 0x188), u32(mm, 0x18c)
        for i in range(sc_cnt):
            e = sc_off + i * 56
            suffix = mm[e + 24:e + 56].split(b'\0')[0].decode()
            p = main + suffix
            if os.path.exists(p):
                self._add_maps(self._open(p))
        self.maps.sort(key=lambda m: m[0])
        img_off, img_cnt = u32(mm, 0x1c0), u32(mm, 0x1c4)
        self.images = []
        for i in range(img_cnt):
            e = img_off + i * 32
            addr = u64(mm, e)
            poff = u32(mm, e + 24)
            path = mm[poff:mm.find(b'\0', poff)].decode()
            self.images.append((addr, path))
        self.images.sort()
        self.img_addrs = [a for a, _ in self.images]
        self.syms = self._open(main + '.symbols') if os.path.exists(main + '.symbols') else None

    def _open(self, p):
        f = open(p, 'rb')
        mm = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
        self.files[p] = mm
        return mm

    def _add_maps(self, mm):
        mo, mc = u32(mm, 0x10), u32(mm, 0x14)
        for i in range(mc):
            e = mo + i * 32
            self.maps.append((u64(mm, e), u64(mm, e + 8), u64(mm, e + 16), mm))

    def read(self, addr, n):
        for a, s, fo, mm in self.maps:
            if a <= addr < a + s:
                o = fo + (addr - a)
                return mm[o:o + n]
        raise KeyError(hex(addr))

    def image_for(self, addr):
        i = bisect.bisect_right(self.img_addrs, addr) - 1
        return self.images[i] if i >= 0 else (None, None)

    def macho(self, mh):
        hdr = self.read(mh, 32)
        ncmds, sizeofcmds = u32(hdr, 16), u32(hdr, 20)
        lc = self.read(mh + 32, sizeofcmds)
        segs, symtab, fstarts = {}, None, None
        o = 0
        for _ in range(ncmds):
            cmd, size = u32(lc, o), u32(lc, o + 4)
            if cmd == 0x19:
                name = lc[o + 8:o + 24].split(b'\0')[0].decode()
                segs[name] = (u64(lc, o + 24), u64(lc, o + 32), u64(lc, o + 40), u64(lc, o + 48))
            elif cmd == 0x2:
                symtab = struct.unpack_from('<IIII', lc, o + 8)
            elif cmd == 0x26:
                fstarts = struct.unpack_from('<II', lc, o + 8)
            o += size
        return segs, symtab, fstarts

    def le_addr(self, segs, off):
        vm, vsz, fo, fsz = segs['__LINKEDIT']
        return vm + (off - fo)

    def symbols(self, mh):
        segs, symtab, fstarts = self.macho(mh)
        out = []
        if symtab:
            symoff, nsyms, stroff, strsize = symtab
            nl = self.read(self.le_addr(segs, symoff), nsyms * 16)
            st = self.read(self.le_addr(segs, stroff), strsize)
            for i in range(nsyms):
                strx, ntype, nsect, ndesc, val = struct.unpack_from('<IBBHQ', nl, i * 16)
                if ntype & 0xe0 == 0 and (ntype & 0x0e) == 0x0e and val:
                    out.append((val, st[strx:st.find(b'\0', strx)].decode(errors='replace')))
        out += self.local_symbols(mh)
        starts = []
        if fstarts:
            data = self.read(self.le_addr(segs, fstarts[0]), fstarts[1])
            addr = segs['__TEXT'][0]
            i = 0
            while i < len(data):
                v, sh = 0, 0
                while True:
                    b = data[i]
                    i += 1
                    v |= (b & 0x7f) << sh
                    sh += 7
                    if not b & 0x80:
                        break
                if v == 0:
                    break
                addr += v
                starts.append(addr)
        out.sort()
        return out, starts

    def local_symbols(self, mh):
        if not self.syms:
            return []
        s = self.syms
        lo, ls = u64(s, 0x48), u64(s, 0x50)
        if not lo:
            return []
        nlo, nlc, so, ssz, eo, ec = struct.unpack_from('<IIIIII', s, lo)
        dyoff = mh - self.base
        res = []
        for i in range(ec):
            e = lo + eo + i * 16
            off, start, cnt = u64(s, e), u32(s, e + 8), u32(s, e + 12)
            if off != dyoff:
                continue
            for j in range(start, start + cnt):
                n = lo + nlo + j * 16
                strx, ntype, nsect, ndesc, val = struct.unpack_from('<IBBHQ', s, n)
                if ntype & 0xe0 == 0 and (ntype & 0x0e) == 0x0e and val:
                    a = lo + so + strx
                    res.append((val, s[a:s.find(b'\0', a)].decode(errors='replace')))
            break
        return res


def main():
    c = Cache(sys.argv[1])
    slide = int(sys.argv[2], 16)
    cache_syms = {}
    for a in sys.argv[3:]:
        rt = int(a, 16)
        addr = rt - slide
        mh, path = c.image_for(addr)
        if mh is None:
            print(f'{a}: not in cache')
            continue
        if mh not in cache_syms:
            cache_syms[mh] = c.symbols(mh)
        syms, starts = cache_syms[mh]
        name = os.path.basename(path)
        vals = [v for v, _ in syms]
        i = bisect.bisect_right(vals, addr) - 1
        fs = starts[bisect.bisect_right(starts, addr) - 1] if starts and starts[0] <= addr else None
        if i >= 0:
            v, sym = syms[i]
            extra = f' (func start +0x{addr - fs:x})' if fs and fs > v else ''
            print(f'{a}: {name} {sym} +0x{addr - v:x}{extra}')
        else:
            print(f'{a}: {name} +0x{addr - mh:x}')


if __name__ == '__main__':
    main()
