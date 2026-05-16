# remember_engine.py

from all_memory import long_term_memory, short_term_memory, remember_fact, recall_fact

def check_and_remember(user_input):
    """
    This checks for phrases like:
    - 'My name is John'
    - 'My favorite food is rice'
    And saves them to memory.
    """
    lowered = user_input.lower()

    if "my name is" in lowered:
        name = lowered.split("my name is")[-1].strip().capitalize()
        remember_fact("user_name", name)
        return f"Okay, I’ll remember your name is {name} 😊"

    elif "my favorite food is" in lowered:
        food = lowered.split("my favorite food is")[-1].strip()
        remember_fact("favorite_food", food)
        return f"Got it! 🍽️ Your favorite food is {food}."

    # Add more memory rules as needed...

    return None  # If nothing matched

def load_identity_response():
    return long_term_memory.get("identity_phrase", "I am your AI assistant.")