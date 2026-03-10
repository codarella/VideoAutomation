"""
Standalone sync map generator.
Reads prompts.json + checks existing images → writes sync_map.txt to scripts/.
Usage: python gen_sync_map.py
"""
import json, os, re

PROMPTS = (r"C:\Users\OVERLORD\Videos\VideoAutomation\video_workspace\scripts"
           r"\9_Particles_Physics_Predicted_and_Whether_They_ve_Been_FoundMP3_prompts.json")
IMAGES_DIR = (r"C:\Users\OVERLORD\Videos\VideoAutomation\video_workspace\images"
              r"\9_Particles_Physics_Predicted_and_Whether_They_ve_Been_FoundMP3")
OUT = (r"C:\Users\OVERLORD\Videos\VideoAutomation\video_workspace\scripts"
       r"\9_Particles_sync_map.txt")

with open(PROMPTS, encoding="utf-8") as f:
    data = json.load(f)

scenes = data["scenes"]
# Scenes in prompts.json start at scene 2; image files are scene_{scene-1:04d}.png
rows = []
missing = 0
for sc in scenes:
    snum = sc["scene"]
    img_name = f"scene_{snum - 1:04d}.png"
    img_path = os.path.join(IMAGES_DIR, img_name)
    exists = os.path.exists(img_path)

    m = re.match(r"([\d.]+)s\s*-\s*([\d.]+)s", sc.get("time", ""))
    if m:
        t0, t1 = float(m.group(1)), float(m.group(2))
    else:
        t0 = t1 = 0.0

    dur = t1 - t0
    status = "OK" if exists else "MISSING"
    if not exists:
        missing += 1
    rows.append((snum, t0, t1, dur, img_name, status))

# Print
print(f"\nSync map -- {len(scenes)} scenes, {missing} MISSING\n")
print(f"  {'#':>4}  {'start':>8}  {'end':>8}  {'dur':>7}  {'status':<8}  file")
print("  " + "-" * 70)
for snum, t0, t1, dur, img_name, status in rows:
    flag = "!" if status == "MISSING" else " "
    print(f"  {snum:>4}  {t0:>8.3f}s  {t1:>8.3f}s  {dur:>6.3f}s  {flag}{status:<7}  {img_name}")

# Save
with open(OUT, "w") as f:
    f.write("scene\tlock_time\tend_time\tduration\tstatus\timage\n")
    for snum, t0, t1, dur, img_name, status in rows:
        f.write(f"{snum}\t{t0:.6f}\t{t1:.6f}\t{dur:.6f}\t{status}\t{img_name}\n")

print(f"\nSaved -> {OUT}")
print(f"   {missing} missing / {len(scenes) - missing} present")
