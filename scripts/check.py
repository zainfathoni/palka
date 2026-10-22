"""Check repository text and local Markdown file links without dependencies."""

from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parent.parent
TEXT_SUFFIXES = {".md", ".py", ".yml", ".yaml"}
TEXT_NAMES = {".gitignore", ".editorconfig", "LICENSE"}
LINK = re.compile(r"!?\[[^\]]*\]\(([^\s)]+)(?:\s+\"[^\"]*\")?\)")


def check() -> list[str]:
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    errors = []
    for name in sorted(set(result.stdout.decode().split("\0")) - {""}):
        path = ROOT / name
        if not path.is_file():
            continue
        if path.suffix not in TEXT_SUFFIXES and path.name not in TEXT_NAMES:
            continue
        data = path.read_bytes()
        try:
            body = data.decode("utf-8")
        except UnicodeDecodeError:
            errors.append(f"{name}: text must be UTF-8")
            continue
        if data and not data.endswith(b"\n"):
            errors.append(f"{name}: missing final newline")
        for number, line in enumerate(body.splitlines(), 1):
            if line.rstrip() != line:
                errors.append(f"{name}:{number}: trailing whitespace")
        if b"\r" in data:
            errors.append(f"{name}: use LF line endings")
        if path.suffix == ".md":
            for match in LINK.finditer(body):
                target = urlsplit(match.group(1))
                if target.scheme or target.netloc or not target.path:
                    continue
                linked = (path.parent / unquote(target.path)).resolve()
                if not linked.is_relative_to(ROOT) or not linked.exists():
                    errors.append(f"{name}: missing local link {match.group(1)}")
    return errors


if __name__ == "__main__":
    failures = check()
    if failures:
        print("\n".join(failures), file=sys.stderr)
        sys.exit(1)
    print("Repository text and local Markdown file links are valid.")
