"""
Small OpenEXR reader for Corona .cxr files (pure Python + numpy + zlib).

Reads the header (including Corona's own attributes) and only the channels you ask for, block by block,
so a 4K file with 90 render elements needs memory for just the requested channels.
Supports single-part scanline files with NONE, RLE, ZIPS and ZIP compression (Corona's default is ZIP).
"""
import struct
import zlib

# zlib-ng (bundled in app/vendor) decompresses the same data about 6x faster than Python's zlib
try:
    import os as _os
    import sys as _sys
    _vendor = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "vendor")
    if _os.path.isdir(_vendor) and _vendor not in _sys.path:
        _sys.path.append(_vendor)
    from zlib_ng import zlib_ng as _fast_zlib
except Exception:
    _fast_zlib = zlib

# exrfast (bundled in app/vendor, 20 lines of C): undoes the predictor + interleaving ~10x faster than numpy
_unpredict = None
try:
    import ctypes as _ct
    _lib = _ct.CDLL(_os.path.join(_vendor, "exrfast.dll" if _os.name == "nt" else "exrfast.so"))
    _lib.exr_unpredict.argtypes = (_ct.c_void_p, _ct.c_void_p, _ct.c_size_t)
    _lib.exr_unpredict.restype = None
    _unpredict = _lib.exr_unpredict
except Exception:
    _unpredict = None

import numpy as np

MAGIC = 20000630
COMPRESSION_NAMES = {0: "none", 1: "RLE", 2: "ZIPS", 3: "ZIP", 4: "PIZ", 5: "PXR24", 6: "B44", 7: "B44A",
                     8: "DWAA", 9: "DWAB", 10: "HTJ2K"}
LINES_PER_BLOCK = {0: 1, 1: 1, 2: 1, 3: 16}
PIXEL_SIZE = {0: 4, 1: 2, 2: 4}          # UINT, HALF, FLOAT
PIXEL_DTYPE = {0: "<u4", 1: "<f2", 2: "<f4"}


class ExrError(Exception):
    pass


def _cstr(buf, pos):
    end = buf.index(b"\0", pos)
    return buf[pos:end].decode("latin-1"), end + 1


def _parse_value(typ, data):
    if typ == "string":
        return data
    if typ == "float":
        return struct.unpack("<f", data)[0]
    if typ == "double":
        return struct.unpack("<d", data)[0]
    if typ == "int":
        return struct.unpack("<i", data)[0]
    if typ in ("compression", "lineOrder", "envmap", "deepImageState"):
        return data[0]
    if typ == "box2i":
        return struct.unpack("<4i", data)
    if typ == "v2f":
        return struct.unpack("<2f", data)
    if typ == "v2i":
        return struct.unpack("<2i", data)
    if typ == "chlist":
        chans, p = [], 0
        while p < len(data) and data[p] != 0:
            name, p = _cstr(data, p)
            ptype, _plin, xs, ys = struct.unpack("<iB3xii", data[p:p + 16])
            p += 16
            chans.append((name, ptype, xs, ys))
        return chans
    return data


class ExrFile:
    def __init__(self, path):
        self.path = path
        with open(path, "rb") as fh:
            head = fh.read(1 << 20)
            if len(head) < 8 or struct.unpack("<i", head[:4])[0] != MAGIC:
                raise ExrError("not an OpenEXR / Corona .cxr file")
            flags = struct.unpack("<I", head[4:8])[0]
            if flags & 0x200 or flags & 0x1000 or flags & 0x800:
                raise ExrError("tiled, multi-part or deep EXR files aren't supported")
            pos = 8
            self.header = {}
            while True:
                while True:
                    try:
                        name, p = _cstr(head, pos)
                        break
                    except ValueError:  # header longer than what we read
                        fh.seek(0)
                        head = fh.read(len(head) * 4)
                if name == "":
                    pos = p
                    break
                typ, p = _cstr(head, p)
                size = struct.unpack("<i", head[p:p + 4])[0]
                p += 4
                if p + size > len(head):
                    fh.seek(0)
                    head = fh.read(p + size + (1 << 20))
                self.header[name] = _parse_value(typ, head[p:p + size])
                pos = p + size
            self.header_end = pos
        dw = self.header["dataWindow"]
        self.x0, self.y0 = dw[0], dw[1]
        self.width, self.height = dw[2] - dw[0] + 1, dw[3] - dw[1] + 1
        self.channels = sorted(self.header["channels"], key=lambda c: c[0].encode())
        self.compression = self.header.get("compression", 0)
        if self.compression not in LINES_PER_BLOCK:
            raise ExrError(f"the file uses {COMPRESSION_NAMES.get(self.compression, '?')} compression; "
                           "save the .cxr with ZIP compression (Corona's default) or no compression")
        for c in self.channels:
            if c[2] != 1 or c[3] != 1:
                raise ExrError("sub-sampled channels aren't supported")
        self.channel_names = [c[0] for c in self.channels]

    def attr(self, name, default=None):
        v = self.header.get(name, default)
        return v.decode("utf-8", "replace") if isinstance(v, bytes) else v

    def read(self, names, progress=None, cancel=None, threads=4):
        """Returns {name: HxW float32} for the requested channels (missing names are skipped)."""
        want = {n for n in names if n in self.channel_names}
        W, H = self.width, self.height
        lines = LINES_PER_BLOCK[self.compression]
        nblocks = (H + lines - 1) // lines
        # byte layout of one scanline: channels in sorted order, each W * size bytes
        layout, off = {}, 0
        for name, ptype, _, _ in self.channels:
            sz = PIXEL_SIZE[ptype] * W
            if name in want:
                layout[name] = (off, sz, PIXEL_DTYPE[ptype])
            off += sz
        line_bytes = off
        out = {n: np.empty((H, W), np.float32) for n in layout}
        with open(self.path, "rb") as fh:
            fh.seek(self.header_end)
            offsets = np.frombuffer(fh.read(8 * nblocks), "<u8")

        def job(block_ids):
            with open(self.path, "rb") as fh:
                for bi in block_ids:
                    if cancel is not None:
                        cancel()
                    fh.seek(int(offsets[bi]))
                    y, size = struct.unpack("<ii", fh.read(8))
                    raw = fh.read(size)
                    y -= self.y0
                    n = min(lines, H - y)
                    data = self._decompress(raw, line_bytes * n)
                    blk = np.frombuffer(data, np.uint8, count=line_bytes * n).reshape(n, line_bytes)
                    for name, (co, sz, dt) in layout.items():
                        out[name][y:y + n] = blk[:, co:co + sz].view(dt)     # converts to float32 on the way
                    done[0] += 1
                    if progress is not None and done[0] % 16 == 0:
                        progress(done[0] / nblocks)

        done = [0]
        workers = max(1, min(threads, nblocks))
        if workers == 1:
            job(range(nblocks))
        else:
            from concurrent.futures import ThreadPoolExecutor
            chunks = [range(i, nblocks, workers) for i in range(workers)]
            with ThreadPoolExecutor(workers) as ex:
                for f in [ex.submit(job, c) for c in chunks]:
                    f.result()
        return out

    def _decompress(self, raw, expected):
        c = self.compression
        if c == 0 or len(raw) == expected:
            return raw
        if c in (2, 3):
            buf = _fast_zlib.decompress(raw)
            if _unpredict is not None:
                res = np.empty(len(buf), np.uint8)
                _unpredict(buf, res.ctypes.data, len(buf))     # the C helper (releases the GIL)
                return res
            t = np.frombuffer(buf, np.uint8)
        elif c == 1:
            t = _rle(raw, expected)
            if _unpredict is not None:
                res = np.empty(len(t), np.uint8)
                _unpredict(t.ctypes.data, res.ctypes.data, len(t))
                return res
        else:
            raise ExrError("unsupported compression")
        # undo the predictor: t[i] = t[i-1] + t[i] - 128 (mod 256); uint8 cumsum wraps mod 256
        d = t - np.uint8(128)
        d[0] = t[0]
        t = np.cumsum(d, dtype=np.uint8)
        # undo the interleaving: first half holds the even bytes, second half the odd bytes
        half = (len(t) + 1) // 2
        res = np.empty(len(t), np.uint8)
        res[0::2] = t[:half]
        res[1::2] = t[half:]
        return res


def _rle(raw, expected):
    out = bytearray()
    i = 0
    while i < len(raw):
        n = struct.unpack("b", raw[i:i + 1])[0]
        i += 1
        if n < 0:
            out += raw[i:i - n]
            i += -n
        else:
            out += raw[i:i + 1] * (n + 1)
            i += 1
    return np.frombuffer(bytes(out), np.uint8)
