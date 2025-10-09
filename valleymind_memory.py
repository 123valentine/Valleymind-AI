import json
import os

MEMORY_FILE = "valleymind_utils/valleymind_memory.json"

# Ensure file exists
if not os.path.exists(MEMORY_FILE):
    with open(MEMORY_FILE, 'w') as f:
        json.dump({}, f)

def load_memory():
    with open(MEMORY_FILE, 'r') as f:
        return json.load(f)

def save_memory(data):
    with open(MEMORY_FILE, 'w') as f:
        json.dump(data, f, indent=4)

def remember(key, value):
    memory = load_memory()
    memory[key.lower()] = value
    save_memory(memory)

def recall(key):
    memory = load_memory()
    return memory.get(key.lower(), None)

def get_all_memories():
    return load_memory()

# Emoji responses
def emoji_reply(text):
    emoji_map = {
        "happy": "😄",
        "sad": "😢",
        "angry": "😡",
        "joke": "😂",
        "hello": "👋",
        "thanks": "🙏",
        "bye": "👋",
        "love": "❤️",
        "creator": "🤖",
    }

    for keyword, emoji in emoji_map.items():
        if keyword in text.lower():
            return f"{text} {emoji}"
    return text