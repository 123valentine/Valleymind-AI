import requests
import os

UPDATE_URL = "https://raw.githubusercontent.com/YOUR_GITHUB_USERNAME/YOUR_REPO_NAME/main/valleymind_ai.py"
LOCAL_FILE = "valleymind_ai.py"

def check_for_updates():
    try:
        response = requests.get(UPDATE_URL)
        if response.status_code == 200:
            remote_code = response.text
            with open(LOCAL_FILE, "r", encoding="utf-8") as f:
                local_code = f.read()

            if local_code.strip() != remote_code.strip():
                print("🔄 Update available. Updating now...")
                with open(LOCAL_FILE, "w", encoding="utf-8") as f:
                    f.write(remote_code)
                print("✅ Updated to latest version.")
                return True
            else:
                print("📦 Already up to date.")
        else:
            print(f"⚠️ Could not check updates. Server response: {response.status_code}")
    except Exception as e:
        print(f"⚠️ Failed to check updates: {e}")