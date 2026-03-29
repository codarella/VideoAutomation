"""
Trim a project JSON to a range of segment numbers and save as a new project.
Usage:
    python trim_project.py
"""
import json
from pathlib import Path

WORKSPACE      = Path(r"c:\Users\OVERLORD\Videos\VideoAutomation\video_workspace")
SOURCE_PROJECT = "Animal Niche One"
OUTPUT_PROJECT = "Animal Niche One Test"
KEEP_SEGMENTS  = {10, 9, 8, 7, 6, 5}   # segment numbers to keep

src = WORKSPACE / "scripts" / f"{SOURCE_PROJECT}_project.json"
dst = WORKSPACE / "scripts" / f"{OUTPUT_PROJECT}_project.json"

with open(src, encoding="utf-8") as f:
    data = json.load(f)

# Filter scenes: keep scenes whose segment_number is in KEEP_SEGMENTS
# Also keep intro scenes (no segment_number) if they fall before the first kept segment
kept_scenes = []
for scene in data["scenes"]:
    seg_num = scene.get("metadata", {}).get("segment_number")
    if seg_num is None:
        # intro scene — keep only if it starts before the first kept segment boundary
        kept_scenes.append(scene)
    elif seg_num in KEEP_SEGMENTS:
        kept_scenes.append(scene)

# Filter aligned_segments
kept_aligned = [
    a for a in data.get("aligned_segments", [])
    if a["number"] in KEEP_SEGMENTS
]

# New audio_duration = end time of the last kept scene
if kept_scenes:
    new_duration = kept_scenes[-1]["end"]
else:
    new_duration = data["audio_duration"]

# Build new project data
new_data = dict(data)
new_data["name"]             = OUTPUT_PROJECT
new_data["scenes"]           = kept_scenes
new_data["aligned_segments"] = kept_aligned
new_data["audio_duration"]   = new_duration
new_data["expected_count"]   = len(KEEP_SEGMENTS)

with open(dst, "w", encoding="utf-8") as f:
    json.dump(new_data, f, indent=2, ensure_ascii=False)

scene_counts = {"content": 0, "number_card": 0, "intro": 0}
for s in kept_scenes:
    scene_counts[s["type"]] = scene_counts.get(s["type"], 0) + 1

print(f"Source : {src.name}")
print(f"Output : {dst.name}")
print(f"Kept segments: {sorted(KEEP_SEGMENTS, reverse=True)}")
print(f"Scenes kept  : {len(kept_scenes)} total "
      f"({scene_counts['content']} content, "
      f"{scene_counts['number_card']} cards, "
      f"{scene_counts.get('intro', 0)} intro)")
print(f"Duration     : {new_duration:.1f}s ({new_duration/60:.1f} min)")
print(f"\nRun the pipeline with:")
print(f'  --name "{OUTPUT_PROJECT}" --start-from prompt --style animals_nature')
