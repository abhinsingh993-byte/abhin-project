#!/usr/bin/env python3
"""
Patch Terraform file(s) so that in every
  resource "aws_vpn_connection" "..." { ... }
block we ensure the following lines exist, are uncommented, and
each pair is kept together with NO blank line between them:

  tunnel1_startup_action = "start"
  tunnel2_startup_action = "start"

  tunnel1_enable_tunnel_lifecycle_control = true
  tunnel2_enable_tunnel_lifecycle_control = true

Rules (per resource block):
  1) If an uncommented line with the desired value exists -> no action.
  2) If present but commented (# or //) -> uncomment and normalize to desired value.
  3) If missing -> add before the closing '}' of the block.
  4) Keep the two startup_action lines adjacent (no blank between).
     Keep the two lifecycle_control lines adjacent (no blank between).
  5) If the attribute exists with a different value, leave as-is unless you pass --enforce
     (legacy alias --enforce-start also supported).

Usage:
  python patch_tf.py --file path/to/vpn_gateway.tf [--dry-run] [--backup] [--enforce]
"""

import argparse
import os
import re
import sys
from datetime import datetime
from typing import List, Tuple

# Desired targets
TARGET_ATTRS = [
    ("tunnel1_startup_action", '"start"'),
    ("tunnel2_startup_action", '"start"'),
    ("tunnel1_enable_tunnel_lifecycle_control", 'true'),
    ("tunnel2_enable_tunnel_lifecycle_control", 'true'),
]

# Pair groupings we want adjacent with no blank line between
GROUPS = [
    ("tunnel1_startup_action", "tunnel2_startup_action"),
    ("tunnel1_enable_tunnel_lifecycle_control", "tunnel2_enable_tunnel_lifecycle_control"),
]

# Detect aws_vpn_connection resource header
RESOURCE_HEADER_RE = re.compile(
    r'^\s*resource\s+"aws_vpn_connection"\s+"[^"]+"\s*\{\s*$', re.IGNORECASE
)

def build_uncommented_exact_re(attr: str, value: str) -> re.Pattern:
    return re.compile(rf'^\s*{re.escape(attr)}\s*=\s*{re.escape(value)}\s*$', re.IGNORECASE)

def build_any_value_uncommented_re(attr: str) -> re.Pattern:
    return re.compile(rf'^\s*{re.escape(attr)}\s*=\s*.+$', re.IGNORECASE)

def build_comment_re(attr: str) -> re.Pattern:
    return re.compile(rf'^\s*(#|//)\s*{re.escape(attr)}\s*=.*$', re.IGNORECASE)

def find_resource_blocks(lines: List[str]) -> List[Tuple[int, int]]:
    """
    Return list of (start_idx, end_idx_inclusive) for each aws_vpn_connection block.
    Simple brace-balance; line-based.
    """
    blocks: List[Tuple[int, int]] = []
    i, n = 0, len(lines)
    while i < n:
        if RESOURCE_HEADER_RE.match(lines[i]):
            depth = 0
            start = i
            j = i
            while j < n:
                depth += lines[j].count("{")
                depth -= lines[j].count("}")
                if depth == 0:
                    blocks.append((start, j))
                    i = j
                    break
                j += 1
        i += 1
    return blocks

def ensure_attributes_in_block(lines: List[str], start: int, end: int, enforce: bool) -> Tuple[bool, int, List[str]]:
    """
    Ensure TARGET_ATTRS exist in lines[start:end+1].
    Returns (modified?, new_end_index, messages).
    """
    modified = False
    msgs: List[str] = []

    # Indentation: pick it from first non-empty inner line else default "  "
    inner_indent = "  "
    for k in range(start + 1, end + 1):
        if lines[k].strip():
            m = re.match(r'^(\s*)', lines[k])
            inner_indent = (m.group(1) if m else "  ") or "  "
            break

    exists_exact = {a: False for a, _ in TARGET_ATTRS}
    commented_idx = {a: None for a, _ in TARGET_ATTRS}
    other_value_idx = {a: None for a, _ in TARGET_ATTRS}
    values = {a: v for a, v in TARGET_ATTRS}

    # Scan current block
    for idx in range(start + 1, end):
        raw = lines[idx]
        for attr, value in TARGET_ATTRS:
            if build_uncommented_exact_re(attr, value).match(raw):
                exists_exact[attr] = True
            elif build_comment_re(attr).match(raw):
                if commented_idx[attr] is None:
                    commented_idx[attr] = idx
            elif build_any_value_uncommented_re(attr).match(raw):
                if other_value_idx[attr] is None:
                    other_value_idx[attr] = idx

    # 1/2/3: uncomment, enforce, or add
    for attr, value in TARGET_ATTRS:
        target_line = f"{attr} = {value}"

        if exists_exact[attr]:
            msgs.append(f"No change: '{target_line}' already present (uncommented).")
            continue

        if other_value_idx[attr] is not None and not enforce:
            msgs.append(
                f"Skipped: '{attr}' present with a different value at line {other_value_idx[attr]+1} "
                f"(use --enforce to normalize to {value})."
            )
            continue

        if other_value_idx[attr] is not None and enforce:
            i = other_value_idx[attr]
            indent = re.match(r'^(\s*)', lines[i]).group(1) or inner_indent
            new_line = f"{indent}{target_line}"
            if lines[i] != new_line:
                lines[i] = new_line
                modified = True
                msgs.append(f"Updated '{attr}' at line {i+1} to {value}.")
            exists_exact[attr] = True
            continue

        if commented_idx[attr] is not None:
            i = commented_idx[attr]
            indent = re.match(r'^(\s*)', lines[i]).group(1) or inner_indent
            new_line = f"{indent}{target_line}"
            if lines[i] != new_line:
                lines[i] = new_line
                modified = True
                msgs.append(f"Uncommented & normalized '{attr}' at line {i+1}.")
            exists_exact[attr] = True
            continue

        # Append just before closing brace
        insert_at = end
        lines.insert(insert_at, f"{inner_indent}{target_line}")
        modified = True
        exists_exact[attr] = True
        msgs.append(f"Appended '{attr}' in resource block (before line {end+1}).")
        end += 1  # block end shifts by one

    # Final pass: keep each pair adjacent (remove blanks between them; do NOT reorder across non-blank/non-comment)
    def idx_of_attr(attr: str) -> int | None:
        pat = build_any_value_uncommented_re(attr)
        for i in range(start + 1, end):
            if pat.match(lines[i]):
                return i
        return None

    def only_comments_between(i1: int, i2: int) -> bool:
        for k in range(i1 + 1, i2):
            s = lines[k].lstrip()
            if not (s == "" or s.startswith("#") or s.startswith("//")):
                return False
        return True

    for a1, a2 in GROUPS:
        i1 = idx_of_attr(a1)
        i2 = idx_of_attr(a2)
        if i1 is None or i2 is None or i2 <= i1:
            continue
        # Remove blank-only lines between the two
        j = i1 + 1
        removed = 0
        while j < i2:
            if lines[j].strip() == "":
                del lines[j]
                modified = True
                removed += 1
                i2 -= 1
            else:
                j += 1

        # If still not adjacent and only comments in between, move the second just after the first
        if i2 != i1 + 1 and only_comments_between(i1, i2):
            line2 = lines.pop(i2)
            lines.insert(i1 + 1, line2)
            modified = True

    return modified, end, msgs

def process_file(path: str, dry_run: bool, backup: bool, enforce: bool) -> int:
    if not os.path.isfile(path):
        print(f"ERROR: file not found: {path}", file=sys.stderr)
        return 1

    with open(path, "r", encoding="utf-8", errors="replace") as f:
        orig = f.read()

    # Normalize to \n for processing
    had_crlf = "\r\n" in orig
    text = orig.replace("\r\n", "\n")
    # Maintain lines WITHOUT trailing '\n' in the list
    lines = text.split("\n")
    keep_terminal_newline = text.endswith("\n")

    blocks = find_resource_blocks(lines)
    if not blocks:
        print("NOTE: No 'aws_vpn_connection' resource blocks found. No changes made.")
        return 0

    overall_modified = False
    all_msgs: List[str] = []
    for (start, end) in blocks:
        changed, end, msgs = ensure_attributes_in_block(lines, start, end, enforce)
        overall_modified |= changed
        all_msgs.extend([f"[{start+1}-{end+1}] {m}" for m in msgs])

    # Reassemble
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
    ap = argparse.ArgumentParser(
        description="Ensure VPN startup_action and lifecycle_control attributes exist, are uncommented, and kept adjacent."
    )
    ap.add_argument("--file", required=True, help="Path to the .tf file to patch (e.g., aws-harness-lab/vpn_gateway.tf).")
    ap.add_argument("--dry-run", action="store_true", help="Preview the changes without writing.")
    ap.add_argument("--backup", action="store_true", help="Create a timestamped .bak copy before writing.")
    ap.add_argument("--enforce", action="store_true",
                    help="Normalize any existing values to the desired ones (\"start\" / true).")
    # Back-compat alias from earlier iterations
    ap.add_argument("--enforce-start", dest="enforce", action="store_true", help=argparse.SUPPRESS)
    args = ap.parse_args()

    sys.exit(process_file(args.file, args.dry_run, args.backup, args.enforce))

if __name__ == "__main__":
    main()
