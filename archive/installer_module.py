import subprocess
import sys
import logging

# Setup log
logging.basicConfig(filename='logs/install_log.txt', level=logging.INFO, format='%(asctime)s %(message)s')
print("🧠 Valleymind is scanning and installing packages...")

# Required packages and preferred versions
dependencies = {
    "transformers": "4.51.3",
    "tokenizers": "0.21.0",  # Compatible with transformers
    "torch": "",  # latest version
    "numpy": "1.26.4",  # Compatible with most
    "pandas": "1.5.3",  # Avoids version conflicts
    "requests": "",
    "gruut": "2.4.0",
    "librosa": "0.11.0",
    "pyttsx3": "",
    "openai": "",
    "pytz": "",
    "huggingface-hub": "",
    "tts": "0.22.0",
    "langflow": "1.4.3",
    "certifi": "2024.8.30"
}

def install_package(package, version=""):
    try:
        name = f"{package}=={version}" if version else package
        subprocess.check_call([sys.executable, "-m", "pip", "install", name])
        logging.info(f"✅ Installed: {name}")
        print(f"✅ Installed: {name}")
    except subprocess.CalledProcessError:
        logging.error(f"❌ Failed to install: {package}")
        print(f"❌ Failed to install: {package}")

for pkg, ver in dependencies.items():
    install_package(pkg, ver)

print("\n🎉 All installation checks completed. Valleymind is ready to grow smarter.")