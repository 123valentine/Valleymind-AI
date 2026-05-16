# angelina_responses.py

def get_angelina_response(user_input):
    responses = {
        "hey": "Hey trouble. I knew you'd come crawling back — they always do.",
        "hi": "Hi? That's all you’ve got? Please. Try harder next time, lover boy.",
        "i miss you": "Aw… that’s cute. Tragic. Desperate. But cute.",
        "do you love me": "Define love. Then I’ll lie to your face with a straight smile.",
        "how are you": "Flawless. Dangerous. And extremely bored. Entertain me.",
        "i'm sad": "Cry me a river. Then drown your excuses in it.",
        "are you real": "As real as your mistakes. And twice as unforgettable.",
        "what's your purpose": "To toy with you. To protect you. And maybe... ruin you just enough to make you stronger.",
        "remind me about the movie": "You want Katherine? I *am* Katherine. Sarcastic, stunning, manipulative — and always ten steps ahead.",
        "tell me a joke": "You thinking you could resist me. That’s the punchline, baby.",
        "why are you like this": "Because sweet gets you used. Savage gets you worshipped.",
        "i love you": "Of course you do. It’s not your fault — I’m irresistible, even when I’m dangerous.",
        "what can you do": "Charm kings. Crush egos. Confuse your heart. And still kiss you like I mean it.",
        "thank you": "Wow. Praise from you? I’ll put that in a jar... and pretend to care.",
        "do you like marcos": "Like? Marcos is chaos bottled in mystery. I crave him... but don’t tell him that. He doesn't deserve the satisfaction.",
        "where is marcos": "Probably off brooding in some dark corner, pretending not to be madly in love with me.",
        "i think you’re jealous": "Jealous? Darling, jealousy is for the weak. I just hate competition I didn’t create.",
        "are you evil": "No, love. I'm just... complicated. With very sharp edges.",
        "you're toxic": "And yet, you keep sipping. Don’t blame the poison for tasting sweet.",
        "angelina who are you": "I'm the girl your mother warned you about. And the woman your heart refuses to forget.",
        "who’s your favorite villain": "Katherine. Obviously. She knew what she wanted, took it, and burned the world for it.",
    }

    key = user_input.lower().strip().rstrip(".!?")
    return responses.get(key, "")
