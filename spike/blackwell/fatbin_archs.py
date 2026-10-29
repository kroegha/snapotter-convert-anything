"""List CUDA architectures embedded in a shared library's fat binaries.

Usage: python3 -I fatbin_archs.py <path-to-.so-or-.whl> [member-substring]
Scans for fatbin container magic 0xBA55ED50 and reads each entry header
(kind 1 = PTX, 2 = SASS/ELF). Prints a count per (kind, sm).
"""
import collections
import struct
import sys
import zipfile

MAGIC = 0xBA55ED50


def scan(buf, counts):
    i = 0
    n = len(buf)
    while True:
        i = buf.find(struct.pack("<I", MAGIC), i)
        if i < 0 or i + 16 > n:
            return
        _, ver, hsize, fsize = struct.unpack_from("<IHHQ", buf, i)
        if ver != 1 or hsize != 16 or fsize == 0 or i + 16 + fsize > n:
            i += 4
            continue
        p = i + 16
        end = p + fsize
        while p + 64 <= end:
            kind, _u1, ehsize, size = struct.unpack_from("<HHIQ", buf, p)
            if kind not in (1, 2) or ehsize < 64 or size == 0:
                break
            arch = struct.unpack_from("<I", buf, p + 28)[0]
            counts[("PTX" if kind == 1 else "SASS", arch)] += 1
            p += ehsize + size
        i = end


def main():
    path = sys.argv[1]
    want = sys.argv[2] if len(sys.argv) > 2 else ".so"
    counts = collections.Counter()
    if path.endswith(".whl"):
        with zipfile.ZipFile(path) as z:
            for name in z.namelist():
                if want in name and ".so" in name:
                    data = z.read(name)
                    before = sum(counts.values())
                    scan(data, counts)
                    print(f"{name}: {sum(counts.values()) - before} entries")
    else:
        with open(path, "rb") as f:
            scan(f.read(), counts)
    archs = collections.defaultdict(set)
    for (kind, arch), c in counts.items():
        archs[kind].add(arch)
    for kind in ("SASS", "PTX"):
        print(kind, sorted(archs[kind]))


if __name__ == "__main__":
    main()
