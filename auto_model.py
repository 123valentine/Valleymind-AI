import requests


def get_latest_groq_model(api_key: str) -> str:
    """
    Fetch the best conversational Groq model.
    Filters out moderation, guard, embedding, and other non-chat models.
    """

    try:
        url = "https://api.groq.com/openai/v1/models"
        headers = {"Authorization": f"Bearer {api_key}"}

        # Added timeout here so the request doesn't hang forever
        response = requests.get(url, headers=headers, timeout=10)

        if response.status_code != 200:
            print("Groq model fetch error:", response.text)
            return "llama-3.3-70b-versatile"

        models = response.json().get("data", [])

        valid_models = []

        for m in models:
            name = m["id"].lower()

            # Block models that are NOT conversational
            if (
                "guard" in name
                or "moderation" in name
                or "embed" in name
                or "whisper" in name
                or "vision" in name
            ):
                continue

            # Allow conversational models
            if (
                "mixtral" in name
                or "llama-3" in name
                or "instruct" in name
                or "chat" in name
            ):
                valid_models.append(m["id"])

        if not valid_models:
            print("⚠️ No valid chat models found. Using fallback.")
            return "llama-3.3-70b-versatile"

        latest = sorted(valid_models)[-1]

        print(f"✅ Active model updated to: {latest}")
        return latest

    except Exception as e:
        print("Error fetching models:", e)
        return "llama-3.3-70b-versatile"