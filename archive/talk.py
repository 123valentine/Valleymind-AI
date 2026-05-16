from moviepy.editor import VideoFileClip, AudioFileClip
from moviepy.audio.fx.all import audio_loop
import os

# -----------------------------
# Config
# -----------------------------
video_file = "Marcos.mp4"
output_video = "Marcos_talking.mp4"
audio_file = "audio.wav"  # David voice generated with generate_audio.py

# -----------------------------
# Step 1: Load video and audio
# -----------------------------
if not os.path.exists(audio_file):
    raise FileNotFoundError(f"{audio_file} not found. Run generate_audio.py first!")
clip = VideoFileClip(video_file)
audio = AudioFileClip(audio_file)

video_duration = clip.duration
audio_duration = audio.duration

print(f"Audio duration: {audio_duration:.2f}s | Video duration: {video_duration:.2f}s")

# -----------------------------
# Step 2: Match audio to video
# -----------------------------
if audio_duration > video_duration:
    # Trim audio if it's longer
    final_audio = audio.subclip(0, video_duration)
else:
    # Loop audio if it's shorter
    final_audio = audio.fx(audio_loop, duration=video_duration)

# -----------------------------
# Step 3: Attach audio to video
# -----------------------------
video_clip = clip.set_audio(final_audio)
print("✅ Audio attached to video.")

# -----------------------------
# Step 4: Export final video
# -----------------------------
video_clip.write_videofile(output_video, codec="libx264", audio_codec="aac")
print(f"🎬 Exported video: {output_video}")

# -----------------------------
# Step 5: Cleanup
# -----------------------------
clip.close()
video_clip.close()
audio.close()
print("Temporary resources released. Done!")