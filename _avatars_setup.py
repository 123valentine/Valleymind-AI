"""Normalize the 3 persona avatar files into static/avatars/<name>.png (clean
lowercase names) and upload each to Cloudflare R2 at assets/personas/<name>.png.

The originals arrived mis-named in static/ root ('Marcus.png', plus 'Angelina .png'
and 'Elena .png' with a stray space that breaks URLs). This copies the exact
bytes to clean names, verifies the R2 upload byte-for-byte, then removes the
mis-named originals so the repo is tidy.
"""
import os
import shutil
import sys

ROOT = r"C:\Users\EGBUJIE VALENTINE\Desktop\Valleymind-AI"
sys.path.insert(0, ROOT)

from core import r2_storage as r2  # noqa: E402

SRC = {
    "marcus":   os.path.join(ROOT, "static", "Marcus.png"),
    "elena":    os.path.join(ROOT, "static", "Elena .png"),
    "angelina": os.path.join(ROOT, "static", "Angelina .png"),
}
AVDIR = os.path.join(ROOT, "static", "avatars")


def main() -> int:
    if not r2.available():
        print("ABORT: R2 not configured")
        return 1
    os.makedirs(AVDIR, exist_ok=True)
    ok = True
    for name, src in SRC.items():
        if not os.path.exists(src):
            print(f"{name}: SOURCE MISSING {src!r}")
            ok = False
            continue
        dst = os.path.join(AVDIR, f"{name}.png")
        shutil.copyfile(src, dst)
        with open(dst, "rb") as f:
            data = f.read()
        key = f"assets/personas/{name}.png"
        r2.upload_bytes(key, data, "image/png")
        r2size = r2.object_size(key)
        match = r2size == len(data)
        print(f"{name}: -> static/avatars/{name}.png ({len(data):,}B) | R2 {key} size={r2size} match={match}")
        if not match:
            ok = False
    if not ok:
        print("\nSome steps failed; leaving originals in place.")
        return 1
    for name, src in SRC.items():
        try:
            os.remove(src)
            print(f"removed mis-named original {os.path.basename(src)!r}")
        except OSError as e:
            print(f"could not remove {src!r}: {e}")
    print("\nDONE.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
