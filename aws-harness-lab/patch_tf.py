
#!/usr/bin/env python3
"""
Ensure these lines exist and are uncommented inside each
Terraform `resource "aws_vpn_connection" ... { }` block:

  tunnel1_startup_action = "start"
  tunnel2_startup_action = "start"

Behavior (per resource block):
  1) If an uncommented line with value "start" exists -> no action.
  2) Else if a commented line (with # or //) exists -> uncomment (and set to "start").
  3) Else -> add the line(s) before the closing brace of the block.

By default, if a different (uncommented) value exists for the attribute,
we LEAVE IT AS-IS to avoid unintended changes. Use `--enforce-start`
to normalize the value to "start".

Usage:
  python patch_tf.py --file path/to/file.tf [--dry-run] [--backup] [--enforce-start]
"""

import argparse
import os
import re
import shutil
import sys
from datetime import datetime
from typing import List, Tuple

TARGET_ATTRS = [
    ("tunnel1_startup_action", '"start"'),
    ("tunnel2_startup_action", '"start"'),
]

# Matches: resource "aws_vpn_connection" "name" {
RESOURCE_HEADER_RE = re.compile(
    r'^\s*resource\s+"aws_vpn_connection"\s+"[^"]+"\s*\{\s*$', re.IGNORECASE
)

def build_uncommented_re(attr: str, value: str) -> re.Pattern:
    return re.compile(
        rf'^\s*{re.escape(attr)}\s*=\s*{re.escape(value)}\s*$',
        re.IGNORECASE
    )

def build_any_value_uncommented_re(attr: str) -> re.Pattern:
    return re.compile(
        rf'^\s*{re.escape(attr)}\s*=\s*.+$',
        re.IGNORECASE
    )

def build_comment_re(attr: str) -> re.Pattern:
    # # or // at start of line; contains the attribute
    return re.compile(
        rf'^\s*(#|//)\s*{re.escape(attr)}\s*=.*$',
        re.IGNORECASE
    )

def find_resource_blocks(lines: List[str]) -> List[Tuple[int, int]]:
    """
    Return list of (start_line_index, end_line_index_inclusive) for each
    aws_vpn_connection resource block. Brace-balanced, line oriented.
    """
    blocks = []
    i = 0
    n = len(lines)
    while i < n:
        if RESOURCE_HEADER_RE.match(lines[i]):
            brace = 0
            start = i
            j = i
            while j < n:
                brace += lines[j].count("{")
                brace -= lines[j].count("}")
                if brace == 0:
                    blocks.append((start, j))
                    i = j
                    break
                j += 1
        i += 1
    return blocks

def ensure_attributes_in_block(lines: List[str], start: int, end: int,
                               enforce_start: bool) -> Tuple[bool, List[str]]:
    """
    Ensure TARGET_ATTRS inside lines[start:end+1].
    Returns (modified?, log_messages).
    """
    modified = False
    msgs = []

    # Choose indentation from first non-empty inner line, fallback to two spaces
    inner_indent = "  "
    for k in range(start + 1, end + 1):
        if lines[k].strip():
            inner_indent = re.match(r'^(\s*)', lines[k]).group(1) or "  "
            if len(inner_indent) == 0:
                inner_indent = "  "
            break

    exists_uncommented = {a: False for a, _ in TARGET_ATTRS}
    exists_commented_idx = {a: None for a, _ in TARGET_ATTRS}
    exists_uncommented_other_value_idx = {a: None for a, _ in TARGET_ATTRS}

    for idx in range(start + 1, end):  # skip header and closing brace
        raw = lines[idx].rstrip("\n")
        for attr, value in TARGET_ATTRS:
            if build_uncommented_re(attr, value).match(raw):
                exists_uncommented[attr] = True
            elif build_comment_re(attr).match(raw):
                if exists_commented_idx[attr] is None:
                    exists_commented_idx[attr] = idx
            elif build_any_value_uncommented_re(attr).match(raw):
                if exists_uncommented_other_value_idx[attr] is None:
                    exists_uncommented_other_value_idx[attr] = idx

    for attr, value in TARGET_ATTRS:
        target_line = f"{attr} = {value}"

        # 3) No action if already present and uncommented with the desired value
        if exists_uncommented[attr]:
            msgs.append(f"No change: '{target_line}' already present (uncommented).")
            continue

        # If present with a different value
        if exists_uncommented_other_value_idx[attr] is not None:
            idx = exists_uncommented_other_value_idx[attr]
            if enforce_start:
                indent = re.match(r'^(\s*)', lines[idx]).group(1)
                new_line = f"{indent}{target_line}\n"
                if lines[idx] != new_line:
                    lines[idx] = new_line
                    modified = True
                    msgs.append(f"Updated '{attr}' at line {idx+1} to {value}.")
            else:
                msgs.append(
                    f"Skipped: '{attr}' present with a different value at line {idx+1} "
                    f"(use --enforce-start to set to {value})."
                )
            continue

        # 2) Uncomment if commented
        if exists_commented_idx[attr] is not None:
            i = exists_commented_idx[attr]
            indent = re.match(r'^(\s*)', lines[i]).group(1) or inner_indent
            new_line = f"{indent}{target_line}\n"
            if lines[i] != new_line:
                lines[i] = new_line
                modified = True
                msgs.append(f"Uncommented & normalized '{attr}' at line {i+1}.")
            else:
                msgs.append(f"No change needed at {i+1} for '{attr}'.")
            continue

        # 1) Add before the block's closing brace
        insert_at = end
        new_line = f"{inner_indent}{target_line}\n"
        lines.insert(insert_at, new_line)
        modified = True
        msgs.append(f"Appended '{attr}' in resource block (before line {end+1}).")
        end += 1  # because we inserted a line

    return modified, msgs

def process_file(path: str, dry_run: bool, backup: bool, enforce_start: bool) -> int:
    if not os.path.isfile(path):
        print(f"ERROR: file not found: {path}", file=sys.stderr)
        return 1

    with open(path, "r", encoding="utf-8", errors="replace") as f:
        orig = f.read()

    had_crlf = "\r\n" in orig
    text = orig.replace("\r\n", "\n")
    lines = text.split("\n")
    keep_terminal_newline = text.endswith("\n")

    blocks = find_resource_blocks(lines)
    if not blocks:
        print("NOTE: No 'aws_vpn_connection' resource blocks found. No changes made.")
        return 0

    overall_modified = False
    all_msgs: List[str] = []
    for (start, end) in blocks:
        changed, msgs = ensure_attributes_in_block(lines, start, end, enforce_start=enforce_start)
        overall_modified = overall_modified or changed
        all_msgs.extend([f"[{start+1}-{end+1}] {m}" for m in msgs])

    new_text = "\n".join(lines)
    if keep_terminal_newline and not new_text.endswith("\n"):
        new_text += "\n"
    if had_crlf:
        new_text = new_text.replace("\n", "\r\n")

    print("Summary:")
    for m in all_msgs:
        print(" - " + m)

    if overall_modified:
        if dry_run:
            print("\nDRY-RUN: Showing preview only; file not written.")
        else:
            if backup:
                ts = datetime.now().strftime("%Y%m%d-%H%M%S")
                bak = f"{path}.{ts}.bak"
                with open(bak, "wb") as b:
                    b.write(orig.encode("utf-8", errors="replace"))
                print(f"Backup created: {bak}")
            with open(path, "wb") as out:
                out.write(new_text.encode("utf-8"))
            print("File updated.")
    else:
        print("No modifications were necessary.")

    return 0

def main():
    ap = argparse.ArgumentParser(description="Ensure VPN startup_action lines exist in aws_vpn_connection resources.")
    ap.add_argument("--file", required=True, help="Path to the .tf file to patch.")
    ap.add_argument("--dry-run", action="store_true", help="Preview the changes without writing.")
    ap.add_argument("--backup", action="store_true", help="Create a timestamped .bak copy before writing.")
    ap.add_argument("--enforce-start", action="store_true",
                    help="If set, normalize any existing values to \"start\".")
    args = ap.parse_args()

    rc = process_file(args.file, dry_run=args.dry_run, backup=args.backup, enforce_start=args.enforce_start)
    sys.exit(rc)

if __name__ == "__main__":
    main()
