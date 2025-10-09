# long_term_memory.py

import json
import os

memory_file = "valleymind_memory.json"

def load_memory():
    if os.path.exists(memory_file):
        with open(memory_file, "r") as f:
            return json.load(f)
    return {}

def save_memory(memory):
    with open(memory_file, "w") as f:
        json.dump(memory, f)

def remember(key, value):
    memory = load_memory()
    memory[key.lower()] = value
    save_memory(memory)

def recall(key):
    memory = load_memory()
    return memory.get(key.lower(), "I can't remember that yet.")

def forget(key):
    memory = load_memory()
    if key.lower() in memory:
        del memory[key.lower()]
        save_memory(memory)

def forget_all():
    save_memory({})       