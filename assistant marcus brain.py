# assistant/marcus/brain.py

class MarcusBrain:
    def __init__(self):
        self.specialty = "Mechanical & Tech Engineering"
        self.personality = "Logical, Technical, Calm"
        self.role = "Explain how to build any tech/mechanical object from scratch"
        self.memory = []
        self.online_learning = True

    def remember(self, info):
        self.memory.append(info)

    def retrieve_memory(self):
        return self.memory

    def is_online(self):
        # Placeholder: Replace with real internet check
        return True

    def silent_learning(self, online_sources):
        if self.is_online():
            for source in online_sources:
                self.memory.append(f"Learned from: {source}")

# Example usage
marcus = MarcusBrain()
marcus.remember("Explained how a generator works.")