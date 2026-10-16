"""Check local Markdown file/directory links without fetching external URLs."""
from pathlib import Path
import re
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parents[1]
LINK = re.compile(r"\[[^\]\n]+\]\(([^)\n]+)\)")


def markdown_files():
    yield from sorted(ROOT.glob("*.md"))
    yield from sorted((ROOT / "docs").rglob("*.md"))
    yield from sorted((ROOT / ".github").rglob("*.md"))


def check():
    failures = []
    checked = 0
    files = list(markdown_files())
    for source in files:
        fence = None
        for number, line in enumerate(source.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.lstrip()
            if stripped.startswith(("```", "~~~")):
                marker = stripped[:3]
                if fence is None:
                    fence = marker
                elif marker == fence:
                    fence = None
                continue
            if fence:
                continue
            for match in LINK.finditer(line):
                target = match.group(1).strip()
                if target.startswith("<") and ">" in target:
                    target = target[1:target.index(">")]
                else:
                    target = re.split(r'\s+["\']', target, maxsplit=1)[0]
                parsed = urlsplit(target)
                if parsed.scheme or parsed.netloc or not parsed.path:
                    continue
                path = unquote(parsed.path)
                resolved = (ROOT / path.lstrip("/") if path.startswith("/") else source.parent / path).resolve()
                checked += 1
                if not resolved.is_relative_to(ROOT) or not resolved.exists():
                    failures.append(f"{source.relative_to(ROOT)}:{number}: missing local target: {target}")
    for failure in failures:
        print(failure)
    print(f"Checked {checked} local links across {len(files)} Markdown files; {len(failures)} missing targets.")
    return bool(failures)


if __name__ == "__main__":
    raise SystemExit(check())
