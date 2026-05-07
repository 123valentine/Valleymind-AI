import os
from flask import Flask, request, jsonify
from flask_cors import CORS

# Load .env using absolute path BEFORE importing brain
# This works even when shell tools can't read .env due to permissions
from dotenv import load_dotenv
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(_BASE_DIR, ".env"))

from core.brain import MarcusBrain  # imported AFTER dotenv so API key is set

app = Flask(__name__)
CORS(app)

# Cache character brains so we don't reload on every request
_characters: dict = {}


def load_character(name: str):
    name = name.lower().strip()

    if name in _characters:
        return _characters[name]

    char_folder = os.path.join(_BASE_DIR, "character", name)
    behavior_path = os.path.join(char_folder, "behavior.json")
    memory_path = os.path.join(char_folder, "memory.json")

    if not os.path.exists(behavior_path):
        print(f"[ERROR] behavior.json not found for '{name}' at {behavior_path}")
        return None

    brain = MarcusBrain(
        memory_file=memory_path,
        behavior_file=behavior_path
    )
    _characters[name] = brain
    return brain


@app.route("/")
def home():
    return "ValleyMind AI backend is running."


@app.route("/chat", methods=["POST"])
def chat():
    try:
        data = request.get_json(silent=True)

        if not data:
            return jsonify({"status": "error", "message": "No JSON body received"}), 400

        message = (data.get("message") or "").strip()
        character_name = (data.get("character") or "marcus").strip()

        if not message:
            return jsonify({"status": "error", "message": "message field is required"}), 400

        character = load_character(character_name)

        if not character:
            return jsonify({
                "status": "error",
                "message": f"Character '{character_name}' not found"
            }), 404

        reply = character.respond(message)

        # Frontend must read data.reply
        return jsonify({
            "status": "success",
            "character": character_name,
            "reply": reply
        })

    except Exception as e:
        print(f"[CRITICAL] /chat crashed: {e}")
        return jsonify({
            "status": "error",
            "message": "Internal server error"
        }), 500


if __name__ == "__main__":
    app.run(debug=True)