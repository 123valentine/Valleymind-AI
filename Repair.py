import os
import subprocess
import json

# === Automatic Repair Tools for Valleymind-AI ===

def install_missing_packages():
    required = ["requests", "pyttsx3", "sounddevice", "vosk", "huggingface_hub"]
    for package in required:
        try:
            __import__(package)
        except ImportError:
            print(f"📦 Installing missing package: {package}")
            subprocess.call(["pip", "install", package])

def check_model_folder():
    if not os.path.exists("model"):
        print("⚠️ Vosk model folder is missing. Please download and unzip into a folder named 'model'.")
        return False
    return True

def check_memory_file():
    if not os.path.exists("memory.json"):
        print("🧠 Creating new memory.json file.")
        with open("memory.json", "w") as f:
            json.dump({}, f)
        return True
    return True

def run_repair():
    print("\n🛠 Running Valleymind-AI auto-repair...\n")
    install_missing_packages()
    check_model_folder()
    check_memory_file()
    print("\n✅ Repair completed.\n")

if __name__ == "__main__":
    run_repair()