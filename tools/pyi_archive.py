"""Minimal PyInstaller 6 onefile CArchive + PYZ reader/writer (Python 3.14).

Used to repackage the v0.4 portable EXE with updated application modules.
Run `python -I pyi_archive.py roundtrip EXE` to prove a byte-exact rebuild.
"""
import marshal
import struct
import sys
import zlib

COOKIE_MAGIC = b'MEI\014\013\012\013\016'
COOKIE = struct.Struct('!8sIIii64s')
ENTRY = struct.Struct('!IIIIBc')


class Package:
    def __init__(self, path):
        self.data = open(path, 'rb').read()
        tail_at = self.data.rfind(COOKIE_MAGIC, len(self.data) - 4096)
        if tail_at < 0:
            raise ValueError('No PyInstaller cookie')
        (_, self.pkg_len, self.toc_off, self.toc_len, self.pyver,
         self.pylib) = COOKIE.unpack_from(self.data, tail_at)
        self.cookie_at = tail_at
        self.trailer = self.data[tail_at + COOKIE.size:]
        self.start = tail_at + COOKIE.size - self.pkg_len
        self.head = self.data[:self.start]
        self.entries = []  # [name, flag, typecode, raw_stored_bytes, ulen]
        at = self.start + self.toc_off
        end = at + self.toc_len
        while at < end:
            elen, off, dlen, ulen, flag, typ = ENTRY.unpack_from(self.data, at)
            name = self.data[at + ENTRY.size:at + elen].rstrip(b'\0').decode('utf-8')
            raw = self.data[self.start + off:self.start + off + dlen]
            self.entries.append([name, flag, typ, raw, ulen])
            at += elen
        self.gap_after = {}

    def get(self, name):
        for entry in self.entries:
            if entry[0] == name:
                return entry
        raise KeyError(name)

    def content(self, name):
        _, flag, _, raw, _ = self.get(name)
        return zlib.decompress(raw) if flag else raw

    def replace(self, name, content, level=9):
        entry = self.get(name)
        entry[3] = zlib.compress(content, level) if entry[1] else content
        entry[4] = len(content)

    def build(self):
        body = bytearray()
        toc = bytearray()
        for name, flag, typ, raw, ulen in self.entries:
            offset = len(body)
            body += raw
            encoded = name.encode('utf-8') + b'\0'
            padded = encoded + b'\0' * (-(ENTRY.size + len(encoded)) % 16)
            toc += ENTRY.pack(ENTRY.size + len(padded), offset, len(raw), ulen, flag, typ) + padded
        toc_off = len(body)
        pkg_len = toc_off + len(toc) + COOKIE.size
        cookie = COOKIE.pack(COOKIE_MAGIC, pkg_len, toc_off, len(toc), self.pyver, self.pylib)
        return self.head + bytes(body) + bytes(toc) + cookie + self.trailer


PYZ_MAGIC = b'PYZ\0'


class Pyz:
    def __init__(self, data):
        if data[:4] != PYZ_MAGIC:
            raise ValueError('Not a PYZ')
        self.pyc_magic = data[4:8]
        (self.toc_off,) = struct.unpack('!i', data[8:12])
        self.header = data[:self.toc_off]  # kept for offsets of untouched data
        toc = marshal.loads(data[self.toc_off:])
        self.toc_type = type(toc)
        self.order = [item[0] for item in toc]
        self.meta = {name: value for name, value in toc}
        self.data = data
        first = min(value[1] for value in self.meta.values())
        self.prefix = data[12:first]  # bytes between header fields and first entry

    def blob(self, name):
        typ, off, length = self.meta[name]
        return self.data[off:off + length]

    def code(self, name):
        return marshal.loads(zlib.decompress(self.blob(name)))

    def build(self, replacements=None):
        replacements = replacements or {}
        out = bytearray(PYZ_MAGIC + self.pyc_magic + b'\0\0\0\0' + self.prefix)
        toc = []
        for name in self.order:
            typ = self.meta[name][0]
            blob = replacements.get(name, self.blob(name))
            toc.append((name, (typ, len(out), len(blob))))
        # Write entries in their original offset order, then the TOC.
        by_offset = sorted(self.order, key=lambda n: self.meta[n][1])
        out = bytearray(PYZ_MAGIC + self.pyc_magic + b'\0\0\0\0' + self.prefix)
        positions = {}
        for name in by_offset:
            blob = replacements.get(name, self.blob(name))
            positions[name] = (len(out), len(blob))
            out += blob
        toc = [(name, (self.meta[name][0],) + positions[name]) for name in self.order]
        toc_off = len(out)
        out += marshal.dumps(self.toc_type(toc) if self.toc_type is not list else toc)
        struct.pack_into('!i', out, 8, toc_off)
        return bytes(out)


def roundtrip(path):
    pkg = Package(path)
    print('entries', len(pkg.entries), 'pyver', pkg.pyver, 'pylib', pkg.pylib.rstrip(b'\0'),
          'head', len(pkg.head), 'trailer', len(pkg.trailer))
    rebuilt = pkg.build()
    print('CArchive byte-exact rebuild:', rebuilt == pkg.data)
    pyz_name = next(e[0] for e in pkg.entries if e[2] == b'z')
    pyz = Pyz(pkg.content(pyz_name))
    print('PYZ', pyz_name, 'modules', len(pyz.order), 'prefix', len(pyz.prefix), 'toc type', pyz.toc_type.__name__)
    print('PYZ byte-exact rebuild:', pyz.build() == pkg.content(pyz_name))
    import importlib.util
    print('pyc magic', pyz.pyc_magic.hex(), 'local', importlib.util.MAGIC_NUMBER.hex())
    for mod in ('attire_swapper_gui', 'fit_helpers', 'garment_fit', 'swap_worker', 'app_updater'):
        if mod in pyz.meta:
            code = pyz.code(mod)
            print('PYZ', mod, pyz.meta[mod], 'co_filename=%r' % code.co_filename)
    for entry in pkg.entries:
        if 'helpers' in entry[0] or entry[2] in (b's', b'z') or entry[0].endswith('.py'):
            print('ENTRY', entry[2], entry[1], len(entry[3]), entry[4], entry[0])


if __name__ == '__main__':
    if sys.argv[1] == 'roundtrip':
        roundtrip(sys.argv[2])
