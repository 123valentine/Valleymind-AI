import os
import subprocess
import sys
import time
from pathlib import Path

# --- CONFIGURATION ---
ROOT = Path(__file__).parent / "core"
EXT = ".mp3"

print("🔍 Checking for locked MP3 files in:", ROOT)

# Step 1: Kill any process that might hold the file open
try:
    print("🧹 Closing background Python or playsound processes...")
    # Use taskkill to kill python.exe and wmplayer if needed
    subprocess.run(["taskkill", "/F", "/IM", "python.exe"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(["taskkill", "/F", "/IM", "wmplayer.exe"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
except Exception as e:
    print(f"(Warning) Could not terminate background processes: {e}")

time.sleep(1)

# Step 2: Unlock and fix permission for each mp3 file
if ROOT.exists():
    mp3_files = list(ROOT.glob(f"*{EXT}"))
    if not mp3_files:
        print("✅ No MP3 files found to fix.")
    else:
        for f in mp3_files:
            print(f"🔧 Fixing permissions for: {f.name}")
            try:
                # Remove read-only attribute (Windows)
                os.system(f'attrib -r "{f}"')
                # Grant full control to current user
                subprocess.run(["icacls", str(f), "/grant", f"{os.getlogin()}:F"], stdout=subprocess.DEVNULL)
                # Change file mode (extra safety)
                os.chmod(f, 0o666)
            except Exception as e:
                print(f"⚠️ Could not change permissions for {f.name}: {e}")
else:
    print("❌ Folder not found:", ROOT)

# Step 3: Optional cleanup of temp files (old marcus_tts_*.mp3)
temp_files = list(ROOT.glob("marcus_tts_*.mp3"))
if temp_files:
    print(f"🧽 Cleaning up {len(temp_files)} temporary audio files...")
    for tf in temp_files:
        try:
            tf.unlink()
            print(f"🗑️ Removed: {tf.name}")
        except Exception as e:
            print(f"⚠️ Could not delete {tf.name}: {e}")

print("\n✅ Permission cleanup complete.")
print("You can now safely run:  python marcus_main.py")