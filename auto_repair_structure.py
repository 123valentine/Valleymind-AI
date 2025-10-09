import os

# Define templates based on filename keywords
TEMPLATES = {
    "music": "# Music system starter\n\ndef play_music():\n    print('Playing music...')\n",
    "video": "# Video system starter\n\ndef create_video():\n    print('Creating video...')\n",
    "editor": "# Video editor\n\ndef edit_video():\n    print('Editing video...')\n",
    "voice": "# Voice system\n\ndef speak(text):\n    print(f'Voice: {text}')\n",
    "obs": "# OBS controller\n\ndef start_obs():\n    print('Starting OBS...')\n",
    "tracker": "# Movie tracker\n\ndef track_movies():\n    print('Tracking movie info...')\n",
    "speak": "# Speaking module\n\ndef speak():\n    print('Speaking...')\n",
    "responses": "# AI response module\n\ndef get_response():\n    return 'Response'\n",
    "config": "# Config loader\n\ndef load_config():\n    return {}\n",
    "test": "# Test module\n\ndef run_tests():\n    print('Running tests...')\n",
    "scanner": "# Scanner logic\n\ndef scan():\n    print('Scanning...')\n",
    "update": "# Update module\n\ndef update():\n    print('Updating system...')\n",
    "repair": "# Repair module\n\ndef repair():\n    print('Running repair...')\n",
    "main": "# Main entry point\n\ndef main():\n    print('Valleymind-AI Starting')\n\nif __name__ == '__main__':\n    main()\n",
}

# Recursively check folders and files
def repair_folder(folder_path):
    for root, dirs, files in os.walk(folder_path):
        py_files = [f for f in files if f.endswith(".py")]

        # If folder has no .py files, add placeholder
        if not py_files:
            placeholder = os.path.join(root, "placeholder.py")
            with open(placeholder, "w") as f:
                f.write("# Placeholder for future module\n")
            print(f"🆕 Created placeholder in {root}")

        for file in py_files:
            full_path = os.path.join(root, file)

            # Check if file is empty
            if os.path.getsize(full_path) == 0:
                base_name = file.lower()
                starter = "# Empty Python file\n"

                # Use keyword in filename to decide what to insert
                for keyword in TEMPLATES:
                    if keyword in base_name:
                        starter = TEMPLATES[keyword]
                        break

                with open(full_path, "w") as f:
                    f.write(starter)
                print(f"✅ Filled empty file: {file}")

if __name__ == "__main__":
    project_root = os.path.dirname(os.path.abspath(__file__))
    repair_folder(project_root)
    print("\n✅ All empty folders and .py files have been checked and patched.")