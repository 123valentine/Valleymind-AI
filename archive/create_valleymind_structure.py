import os

def create_valleymind_folders(base_path="."):
    folders = [
        "plugins",
        "modules/voice",
        "modules/vision",
        "modules/memory",
        "future_data",
        "logs",
        "tech_sketches",
        "emergency_system",
        "valleymind_brain/updates",
        "valleymind_brain/training_data",
    ]
    
    for folder in folders:
        full_path = os.path.join(base_path, folder)
        os.makedirs(full_path, exist_ok=True)
        print(f"✅ Created: {full_path}")

if __name__ == "__main__":
    print("🧠 Valleymind is building her base system...")
    create_valleymind_folders()
    print("\n🎉 All core folders created successfully! Now she’s ready to start learning and growing.")