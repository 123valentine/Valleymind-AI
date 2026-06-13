import uuid
from dotenv import load_dotenv
from core.brain import MarcusBrain

load_dotenv()


def simulate_ui_flow():
    print("⚡ [UI SIMULATION] Initializing Marcus Brain Core...")
    brain = MarcusBrain()

    mock_chat_id = str(uuid.uuid4())
    print(f"🟢 [UI CLICK] Generated fresh thread session ID: {mock_chat_id}")

    test_message = "Can you give me a summary of Liverpool FC transfer strategies for this window?"
    print(f"✉️  [UI SEND] User Message: \"{test_message}\"")

    print("🧠 [BACKEND ENGINE] Orchestrating memory lookups and safety rules...")
    response_payload = brain.respond(
        message=test_message,
        chat_id=mock_chat_id,
        mongo_history=[],
    )

    print("\n==================== UI RENDER VERIFICATION ====================")
    print(f"🤖 [RENDER RESPONSE]: {response_payload[:150]}...")
    print("----------------------------------------------------------------")

    print("📊 [SIDEBAR TITLE SYNC CHECK]:")
    try:
        sessions = getattr(brain.memory, "list_sessions", lambda: [])()
        matched_title = "Untitled Thread"
        for s in sessions:
            if s.get("chat_id") == mock_chat_id:
                matched_title = s.get("title", "No title key found")
                break
        print(f"   👉 Current Sidebar State Title: \"{matched_title}\"")
        if matched_title not in ("New Chat", "Untitled Thread"):
            print("\n🎉 SUCCESS: Frontend/Backend handshake verified! Title generated and synced seamlessly.")
        else:
            print("\n⚠️ NOTE: Logic ran, but title string remained default.")
    except Exception as e:
        print(f"   ❌ Could not verify titles array state: {e}")


if __name__ == "__main__":
    simulate_ui_flow()