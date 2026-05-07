
import os
from core.brain import MarcusBrain

class SelfUpdater:
    def __init__(self, brain):
        self.brain = brain
        self.update_folder = "../update_queue/"

    def scan_updates(self):
        if not os.path.exists(self.update_folder):
            os.makedirs(self.update_folder)
        updates = [f for f in os.listdir(self.update_folder) if f.endswith(".txt")]
        return updates

    def apply_updates(self):
        updates = self.scan_updates()
        for file in updates:
            path = os.path.join(self.update_folder, file)
            with open(path, "r") as f:
                content = f.read()
                self.brain.add_memory("long_term", f"Update from {file}: {content}")
            os.remove(path)
        if updates:
            print(f"Applied updates: {updates}")
        else:
            print("No new updates found.")
