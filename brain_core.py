import json
import datetime

class Emotion:
    def __init__(self, name, strength=0.0):
        self.name = name
        self.strength = strength

    def update(self, value):
        self.strength += value
        self.strength = max(-1.0, min(1.0, self.strength))  # Keep between -1 and 1

    def describe(self):
        if self.strength > 0.7:
            return f"Very {self.name}"
        elif self.strength > 0.3:
            return f"Somewhat {self.name}"
        elif self.strength > 0:
            return f"Slightly {self.name}"
        elif self.strength < -0.7:
            return f"Very not {self.name}"
        elif self.strength < -0.3:
            return f"Somewhat not {self.name}"
        elif self.strength < 0:
            return f"Slightly not {self.name}"
        else:
            return f"Neutral about {self.name}"

class AIBrain:
    def __init__(self, name, personality_type):
        self.name = name
        self.personality_type = personality_type
        self.memory = []
        self.emotions = {
            "love": Emotion("loving"),
            "sadness": Emotion("sad"),
            "anger": Emotion("angry"),
            "curiosity": Emotion("curious"),
            "loyalty": Emotion("loyal"),
            "jealousy": Emotion("jealous")
        }

    def remember(self, event):
        timestamp = datetime.datetime.now().isoformat()
        self.memory.append({"event": event, "timestamp": timestamp})
        print(f"[{self.name}] remembered: {event}")

    def feel(self, emotion, value):
        if emotion in self.emotions:
            self.emotions[emotion].update(value)
            print(f"[{self.name}] feels more {self.emotions[emotion].describe()}")

    def recall(self):
        return [m["event"] for m in self.memory]

    def describe_feelings(self):
        return {e: self.emotions[e].describe() for e in self.emotions}

    def personality_response(self, message):
        if self.personality_type == "philosophical":
            return f"{self.name} (deep): Let me think about that... {message}"
        elif self.personality_type == "calm":
            return f"{self.name} (calm): I understand... {message}"
        elif self.personality_type == "sarcastic":
            return f"{self.name} (sarcastic): Oh wow, so original... {message}"
        else:
            return f"{self.name}: {message}"

    def save_memory(self, filename):
        with open(filename, "w") as f:
            json.dump(self.memory, f)

    def load_memory(self, filename):
        try:
            with open(filename, "r") as f:
                self.memory = json.load(f)
        except FileNotFoundError:
            self.memory = []