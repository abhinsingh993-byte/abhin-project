
#!/usr/bin/env python3
import re, sys, io, argparse, os

parser = argparse.ArgumentParser()
parser.add_argument("--file", required=True, help="Path to a .tf file (relative to repo root)")
args = parser.parse_args()

line = 'instance_tenancy = "default"'
if not os.path.exists(args.file):
    print(f"File not found: {args.file}", file=sys.stderr)
    sys.exit(1)

text = io.open(args.file, 'r', encoding='utf-8').read()
pattern = re.compile(r'(resource\s+"aws_vpc"\s+"main"\s*\{)(.*?)(\n\})', re.DOTALL)

def add_line(m):
    start, body, end = m.groups()
    flex = re.compile(re.sub(r'\s+', r'\\s*', re.escape(line)))
    if flex.search(body):  # already present
        return m.group(0)
    return f"{start}{body}\n  {line}{end}"

new = pattern.sub(add_line, text)
if new != text:
    io.open(args.file, 'w', encoding='utf-8', newline='').write(new)
    print(f"UPDATED: {args.file}")
else:
    print("No change needed.")
