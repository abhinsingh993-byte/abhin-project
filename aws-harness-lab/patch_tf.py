#!/usr/bin/env python3
"""
Ensure the following lines exist and are uncommented inside each
Terraform `resource "aws_vpn_connection" ... { }` block:

  tunnel1_startup_action = "start"
  tunnel2_startup_action = "start"
  tunnel1_enable_tunnel_lifecycle_control = true
  tunnel2_enable_tunnel_lifecycle_control = true

Rules (per resource):
  1) If an uncommented line with the desired value exists -> no action.
  2) Else if a commented line exists (# or //) -> uncomment and normalize to desired value.
  3) Else -> add the line before the closing brace of the block.

If an attribute is present with a different value, it is left unchanged unless
you pass --enforce (or the legacy alias --enforce-start), which normalizes it.

Usage:
  python patch_tf.py --file path/to/file.tf [--dry-run] [--backup] [--enforce]

Exit codes:
  0 on success, 1 on file-not-found or write error.
"""

import argparse
import os
import re
import sys
from datetime import datetime
from typing import List, Tuple

TARGET_ATTRS = [
    ("tunnel1_startup_action", '"start"'),
    ("tunnel2_startup_action", '"start"'),
    ("tunnel1_enable_tunnel_lifecycle_control", 'true'),
    ("tunnel2_enable_tunnel_lifecycle_control", 'true'),
]

# Matches: resource "aws_vpn_connection" "name" {
RESOURCE_HEADER_RE = re.compile(
    r'^\s*resource\s+"aws_vpn_connection"\s+"[^"]+"\s*\{\s*$',
    re.IGNORECASE,
)

def build_uncommented_exact_re(attr: str, value: str) -> re.Pattern:
    # exact desired value (flexible whitespace)
    return re.compile(
        rf'^\s*{re.escape(attr)}\s*=\s*{re.escape(value)}\s*$',
        re.IGNORECASE,
    )

def build_any_value_uncommented_re(attr: str) -> re.Pattern:
    # same attr with any value
    return re.compile(
        rf'^\s*{re.escape(attr)}\s*=\s*.+$',
        re.IGNORECASE,
    )

def build_comment_re(attr: str) -> re.Pattern:
    # line starts with # or // (after optional indent) and mentions attr =
    return re.compile(
        rf'^\s*(#|//)\s*{re.escape(attr)}\s*=.*$',
        re.IGNORECASE,
    )

def find_resource_blocks(lines: List[str]) -> List[Tuple[int, int]]:
    """
    Return list of (start_idx, end_idx_inclusive) for each aws_vpn_connection block.
    Simple brace-balance approach (line-based).
    """
    blocks = []
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

def ensure_attributes_in_block(lines: List[str], start: int, end: int,
                               enforce: bool) -> Tuple[bool, List[str]]:
    """
    Ensure TARGET_ATTRS inside lines[start:end+1].
    Returns (modified?, messages).
    """
    modified = False
    msgs: List[str] = []

    # Pick indentation from first non-empty inner line (else default "  ")
    inner_indent = "  "
    for k in range(start + 1, end + 1):
        if lines[k].strip():
            inner_indent = re.match(r'^(\s*)', lines[k]).group(1) or "  "
            break

    exists_exact = {a: False for a, _ in TARGET_ATTRS}
    commented_idx = {a: None for a, _ in TARGET_ATTRS}
    other_value_idx = {a: None for a, _ in TARGET_ATTRS}

    for idx in range(start + 1, end):  # skip header and closing brace
        raw = lines[idx].rstrip("\n")
        for attr, value in TARGET_ATTRS:
            if build_uncommented_exact_re(attr, value).match(raw):
                exists_exact[attr] = True
            elif build_comment_re(attr).match(raw):
                if commented_idx[attr] is None:
                    commented_idx[attr] = idx
            elif build_any_value_uncommented_re(attr).match(raw):
                if other_value_idx[attr] is None:
                    other_value_idx[attr] = idx

    for attr, value in TARGET_ATTRS:
        desired_line = f"{attr} = {value}"

        # 3) No action if already correct
        if exists_exact[attr]:
            msgs.append(f"No change: '{desired_line}' already present (uncommented).")
            continue

        # If present with a different value
        if other_value_idx[attr] is not None:
            idx = other_value_idx[attr]
            if enforce:
                indent = re.match(r'^(\s*)', lines[idx]).group(1)
                new_line = f"{indent}{desired_line}\n"
                if lines[idx] != new_line:
                    lines[idx] = new_line
                    modified = True
                    msgs.append(f"Updated '{attr}' at line {idx+1} to {value}.")
            else:
                msgs.append(
                    f"Skipped: '{attr}' present with a different value at line {idx+1} "
                    f"(use --enforce to set to {value})."
                )
            continue

        # 2) Uncomment and normalize if commented
        if commented_idx[attr] is not None:
            i = commented_idx[attr]
            indent = re.match(r'^(\s*)', lines[i]).group(1) or inner_indent
            new_line = f"{indent}{desired_line}\n"
            if lines[i] != new_line:
                lines[i] = new_line
                modified = True
                msgs.append(f"Uncommented & normalized '{attr}' at line {i+1}.")
            else:
                msgs.append(f"No change needed at {i+1} for '{attr}'.")
            continue

        # 1) Add before closing brace of the block
        insert_at = end
        new_line = f"{inner_indent}{desired_line}\n"
        lines.insert(insert_at, new_line)
        modified = True
        msgs.append(f"Appended '{attr}' in resource block (before line {end+1}).")
        end += 1  # adjust because we inserted a line

    return modified, msgs

def process_file(path: str, dry_run: bool, backup: bool, enforce: bool) -> int:
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
        changed, msgs = ensure_attributes_in_block(lines, start, end, enforce)
        overall_modified |= changed
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
    ap = argparse.ArgumentParser(
        description="Ensure VPN startup_action and lifecycle_control attributes exist and are uncommented."
    )
    ap.add_argument("--file", required=True, help="Path to the .tf file to patch.")
    ap.add_argument("--dry-run", action="store_true", help="Preview changes without writing.")
    ap.add_argument("--backup", action="store_true", help="Create a timestamped .bak before writing.")
    # Primary flag
    ap.add_argument("--enforce", action="store_true",
                    help="Normalize existing attributes to the desired values.")
    # Legacy alias to keep backward compatibility with earlier instructions
    ap.add_argument("--enforce-start", dest="enforce", action="store_true",
                    help=argparse.SUPPRESS)
    args = ap.parse_args()

    sys.exit(process_file(args.file, args.dry_run, args.backup, args.enforce))

if __name__ == "__main__":
    main()
