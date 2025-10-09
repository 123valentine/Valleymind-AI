# memory.py
import json
import os

class LongTermMemory:
    def __init__(self, memory_file="memory.json"):
        self.memory_file = memory_file
        self.data = self.load_memory()

    def load_memory(self):
        if os.path.exists(self.memory_file):
            with open(self.memory_file, "r") as f:
                return json.load(f)
        return {}

    def save_memory(self):
        with open(self.memory_file, "w") as f:
            json.dump(self.data, f, indent=2)

    def remember(self, key, value):
        self.data[key] = value
        self.save_memory()

    def recall(self, key):
        return self.data.get(key, "I don't remember that yet.")