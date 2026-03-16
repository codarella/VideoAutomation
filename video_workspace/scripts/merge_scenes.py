"""Merge adjacent content scenes in the first 60 seconds to reach ~4s duration each."""
import json
import shutil
from pathlib import Path

FILE = Path(r"c:\Users\OVERLORD\Videos\VideoAutomation\video_workspace\scripts\IronClad_project.json")
BAK = FILE.with_suffix(".json.bak")

# Backup
shutil.copy2(FILE, BAK)
print(f"Backup created: {BAK}")

# Load
with open(FILE, "r", encoding="utf-8") as f:
    data = json.load(f)

scenes = data["scenes"]

# --- BEFORE stats ---
print("\n=== BEFORE ===")
before_first60 = [s for s in scenes if s["start"] < 60.0]
before_content_first60 = [s for s in before_first60 if s["type"] == "content"]
print(f"Total scenes: {len(scenes)}")
print(f"Scenes starting before 60s: {len(before_first60)}")
print(f"Content scenes before 60s: {len(before_content_first60)}")
for s in before_first60:
    dur = round(s["end"] - s["start"], 2)
    print(f"  {s['id']:25s}  type={s['type']:12s}  {s['start']:7.2f}-{s['end']:7.2f}  dur={dur:.2f}s")

# --- Merge logic ---
# Separate scenes into: those starting < 60s and those starting >= 60s
early_scenes = [s for s in scenes if s["start"] < 60.0]
late_scenes = [s for s in scenes if s["start"] >= 60.0]

# Process early scenes: merge adjacent content scenes
merged_early = []
i = 0
while i < len(early_scenes):
    scene = early_scenes[i]

    if scene["type"] != "content":
        merged_early.append(scene)
        i += 1
        continue

    # Start accumulating content scenes
    group = [scene]
    j = i + 1
    while j < len(early_scenes) and early_scenes[j]["type"] == "content":
        candidate = early_scenes[j]
        current_dur = group[-1]["end"] - group[0]["start"]
        added_dur = candidate["end"] - group[0]["start"]

        if current_dur >= 3.8:
            # Already long enough, stop
            break

        if added_dur >= 6.0:
            # Would be too long, stop
            break

        group.append(candidate)
        j += 1

    # Merge the group into one scene
    merged = {
        "id": group[0]["id"],  # will be renumbered later
        "type": "content",
        "start": group[0]["start"],
        "end": group[-1]["end"],
        "text": " ".join(s["text"] for s in group),
        "words": [],
        "prompt": group[0]["prompt"],
        "prompt_source": group[0].get("prompt_source"),
        "image_path": group[0]["image_path"],
        "status": group[0]["status"],
        "include_character": group[0].get("include_character", False),
        "metadata": group[0].get("metadata", {}),
    }
    for s in group:
        merged["words"].extend(s["words"])

    merged_early.append(merged)
    i = j

# Combine
all_scenes = merged_early + late_scenes

# --- Renumber IDs ---
# Track current segment for numbering
seg_counters = {}
for scene in all_scenes:
    if scene["type"] == "intro":
        scene["id"] = "intro"
        continue

    meta = scene.get("metadata", {})
    seg_num = meta.get("segment_number")
    if seg_num is None:
        # Don't renumber scenes without segment info
        continue

    if scene["type"] == "number_card":
        scene["id"] = f"seg{seg_num}_card"
        seg_counters[seg_num] = 0  # reset counter for this segment
        continue

    if scene["type"] == "content":
        count = seg_counters.get(seg_num, 0)
        scene["id"] = f"seg{seg_num}_scene{count:02d}"
        seg_counters[seg_num] = count + 1

data["scenes"] = all_scenes

# --- AFTER stats ---
print("\n=== AFTER ===")
after_first60 = [s for s in all_scenes if s["start"] < 60.0]
after_content_first60 = [s for s in after_first60 if s["type"] == "content"]
print(f"Total scenes: {len(all_scenes)}")
print(f"Scenes starting before 60s: {len(after_first60)}")
print(f"Content scenes before 60s: {len(after_content_first60)}")
for s in after_first60:
    dur = round(s["end"] - s["start"], 2)
    print(f"  {s['id']:25s}  type={s['type']:12s}  {s['start']:7.2f}-{s['end']:7.2f}  dur={dur:.2f}s")

print(f"\nContent scenes merged: {len(before_content_first60)} -> {len(after_content_first60)}")

# Save
with open(FILE, "w", encoding="utf-8") as f:
    json.dump(data, f, indent=2, ensure_ascii=False)

print(f"\nSaved to {FILE}")
