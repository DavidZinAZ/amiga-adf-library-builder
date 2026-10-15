"""Read-only candidate comparison and saved curation media decisions."""
from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path


def deduplicate_candidates(candidates: list[dict]) -> list[dict]:
    """Collapse byte-identical copies within one purpose, retaining locations."""
    unique = {}
    for original in candidates:
        item = dict(original)
        path = Path(item.get("path") or "")
        try:
            if item.get("content"):
                digest = hashlib.sha256(item["content"].encode("utf-8")).hexdigest()
            else:
                if not path.is_file() or path.stat().st_size > 64 * 1024 * 1024:
                    continue
                with path.open("rb") as stream:
                    digest = hashlib.file_digest(stream, "sha256").hexdigest()
        except OSError:
            continue
        key = (item.get("purpose", "manual"), digest)
        item["sha256"] = digest
        locations = list(dict.fromkeys(item.get("locations", []) + ([str(path)] if item.get("path") else [])))
        if key in unique:
            unique[key]["locations"] = list(dict.fromkeys(unique[key]["locations"] + locations))
            if item.get("score", 0) > unique[key].get("score", 0):
                locations = unique[key]["locations"]
                unique[key] = item
                item["locations"] = locations
        else:
            item["locations"] = locations
            unique[key] = item
    return sorted(unique.values(), key=lambda x: (x.get("purpose", ""), -x.get("score", 0), x.get("path", "")))


def latest_state_path(library_root: Path | None) -> Path | None:
    if library_root is None:
        return None
    paths = list((Path(library_root) / "curation").glob("library_state_*.json"))
    return max(paths, key=lambda p: (p.stat().st_mtime_ns, p.name), default=None)


def saved_entry(library_root: Path | None, release_key: str):
    """Read the existing curation authority; never create a second store."""
    path = latest_state_path(library_root)
    if path is None:
        return None
    return _read_state(str(path), path.stat().st_mtime_ns).releases.get(release_key)


@lru_cache(maxsize=4)
def _read_state(path: str, modified: int):
    from .library_state import CurationStateManager
    return CurationStateManager(Path(path)).load()


def source_from_candidate(item: dict):
    from .rtfm import RtfmSource, CATEGORY_MANUALS
    path = Path(item.get("path") or "")
    if item.get("sha256"):
        if item.get("content"):
            digest = hashlib.sha256(item["content"].encode("utf-8")).hexdigest()
        else:
            with path.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if digest != item["sha256"]:
            raise ValueError("Selected manual has changed; review the choice again.")
    return RtfmSource(path=path, root=Path(item.get("root") or path.parent),
                      category=(CATEGORY_MANUALS if item.get("category") == "Manual"
                                else item.get("category") or CATEGORY_MANUALS),
                      stem=item.get("stem") or path.stem,
                      doc_type=item.get("doc_type"), provider=item.get("provider", ""),
                      source_url=item.get("source_url", ""), content=item.get("content", ""))


def manual_candidates(scored) -> list[dict]:
    items = []
    for source, score in scored:
        if not score.matched:
            continue
        items.append({"path": str(source.path) if source.path.is_file() else "",
                      "root": str(source.root), "stem": source.stem,
                      "purpose": "manual", "category": source.category,
                      "doc_type": source.doc_type, "provider": source.provider,
                      "source_url": source.source_url, "content": source.content,
                      "score": score.confidence, "method": score.kind,
                      "evidence": score.evidence})
    return deduplicate_candidates(items)


def pending_purposes(entry) -> set[str]:
    counts = {}
    for candidate in entry.media_candidates:
        purpose = candidate.get("purpose", "manual")
        counts[purpose] = counts.get(purpose, 0) + 1
    return {purpose for purpose, count in counts.items()
            if count > 1 and purpose not in entry.media_choices}


def selected_image(choices: dict) -> dict | None:
    from .local_media import DEFAULT_PREFERRED_TYPES
    purposes = sorted((k for k in choices if k != "manual"),
                      key=lambda k: (DEFAULT_PREFERRED_TYPES.index(k) if k in DEFAULT_PREFERRED_TYPES else 99, k))
    return next((choices[k][0] for k in purposes if choices[k]), None)


def render_manual_choices(group, choices: list[dict], output_dir: Path) -> bytes:
    from .rtfm import build_rtfm_for_group, RtfmConfig
    result = build_rtfm_for_group(group, cfg=RtfmConfig(enabled=True), rtfm_dir=output_dir,
                                  explicit_sources=[source_from_candidate(c) for c in choices])
    if not result.written or not result.rtfm_path:
        raise ValueError("Selected manual could not be converted: " + "; ".join(result.notes))
    return result.rtfm_path.read_bytes()
