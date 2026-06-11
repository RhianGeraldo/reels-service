import sys

with open("lib/pipeline.py", "r") as f:
    lines = f.readlines()

new_lines = []
in_dynamic_block = False

for i, line in enumerate(lines):
    if line.startswith("def run_pipeline("):
        new_lines.append(line)
        continue
    
    if line.startswith("    generate_overlays: bool = True,"):
        new_lines.append(line)
        new_lines.append("    dynamic_editing: bool = True,\n")
        continue

    if line.strip() == "# ===== 4. TRANSCRIBE (WHISPER) =====":
        new_lines.append("        if dynamic_editing:\n")
        in_dynamic_block = True

    if line.strip() == "# ===== 10. CAPTIONS (ASS) =====":
        in_dynamic_block = False
        new_lines.append("        else:\n")
        new_lines.append("            print('[REELS] Bypassing dynamic editing. Pass-through mode active.', flush=True)\n")
        new_lines.append("            noCaption_path = nosilence_path\n")
        new_lines.append("            sfx_track_path = None\n")
        new_lines.append("            hook_l1 = ''\n")
        new_lines.append("            hook_l2 = ''\n")
        new_lines.append("            full_text = ''\n\n")

    if in_dynamic_block:
        if line == "\n":
            new_lines.append(line)
        else:
            new_lines.append("    " + line)
    else:
        new_lines.append(line)

with open("lib/pipeline.py", "w") as f:
    f.writelines(new_lines)
