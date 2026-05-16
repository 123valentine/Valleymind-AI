# emoji_utils.py

emoji_map = {
    "greeting": "👋😊",
    "error": "⚠️❗",
    "success": "✅🎉",
    "thinking": "🤔💭",
    "searching": "🔍🌐",
    "goodbye": "👋😢",
    "thanks": "🙏😊",
    "joke": "😂🤣",
    "fact": "📘✨",
    "love": "❤️😘",
    "warning": "🚨⚠️",
    "ai": "🤖💡",
    "motivational": "💪🔥",
    "fun": "🎮🎉",
    "weather": "☀️🌧️⛅",
    "time": "⏰⌛",
    "date": "📅🗓️"
}

def get_emoji(category):
    return emoji_map.get(category, "🤖")