#!/usr/bin/env python3
"""Render gallery.html: every demo in this repo, its artifact and its source.

Reads gallery.json for the repo-level framing, then derives one card per
source file. Titles and blurbs come from catalog.json when the repo has one
and from the README table otherwise, so the gallery never becomes a second
place to maintain the same prose.

Stdlib only, and the page references out/ relatively — open gallery.html
straight from a checkout and it works, no server and no build step.
"""
from __future__ import annotations

import argparse
import html
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "gallery.json"
OUTPUT = ROOT / "gallery.html"

MAX_BLOCK_LINES = 200
MAX_ARTIFACTS = 4

IMAGE_EXT = {".png", ".svg", ".gif", ".webp", ".jpg", ".jpeg", ".avif"}
VIDEO_EXT = {".mp4", ".webm"}
RENDERABLE = IMAGE_EXT | VIDEO_EXT


# --------------------------------------------------------------------------
# discovery


def tracked_files() -> set:
    """Artifacts committed to git.

    The gallery has to look right in a fresh clone, so a committed render
    always beats an uncommitted one — that is the difference between a page
    of figures and a page of broken images.
    """
    try:
        out = subprocess.run(
            ["git", "-C", str(ROOT), "ls-files"],
            capture_output=True, text=True, timeout=20,
        )
    except (OSError, subprocess.SubprocessError):
        return set()
    return set(out.stdout.split()) if out.returncode == 0 else set()


TRACKED = None


def natural_key(text: str) -> list:
    """Sort 'fig2' before 'fig10'."""
    return [int(p) if p.isdigit() else p.lower() for p in re.split(r"(\d+)", text)]


def load_config() -> dict:
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    cfg.setdefault("artifact_prefix", "{stem}")
    cfg.setdefault("prefer", ["svg", "png", "gif", "webp", "jpg", "mp4"])
    cfg.setdefault("backdrop", "checker")
    cfg.setdefault("rebuild", "just build")
    cfg.setdefault("artifact_dirs", ["out"])
    cfg.setdefault("artifact_exclude", "")
    return cfg


def load_catalog() -> dict:
    path = ROOT / "catalog.json"
    if not path.exists():
        return {}
    return {e["id"]: e for e in json.loads(path.read_text(encoding="utf-8"))}


def load_readme_blurbs() -> dict:
    """Map src path -> the last cell of the README table row that links to it.

    Every repo here documents its demos in one markdown table; parsing it keeps
    the README the single source of truth for what each demo shows.
    """
    path = ROOT / "README.md"
    if not path.exists():
        return {}
    blurbs = {}
    link = re.compile(r"\[[^\]]*\]\((src/[^)]+)\)")
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.lstrip().startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        hit = None
        for cell in cells:
            m = link.search(cell)
            if m:
                hit = m.group(1).rstrip("/")
                break
        if not hit:
            continue
        tail = [c for c in cells if c and not link.search(c) and not c.isdigit()]
        if tail:
            blurbs[hit] = tail[-1]
    return blurbs


def extract_guarded(cfg: dict, demo_id: str, all_ids: list[str]) -> tuple[str, str] | None:
    """Pull one demo out of a bundle that dispatches on a scene guard.

    Several single-file demos are written as `if (scene === 'hexagons') { ... }`
    rather than as named functions. Splitting the file at every known scene
    guard is more reliable than counting braces through minified TypeScript.
    """
    template = cfg.get("code_guard")
    glob = cfg.get("code_search")
    if not template or not glob:
        return None
    for path in sorted(ROOT.glob(glob), key=lambda p: natural_key(p.name)):
        text = path.read_text(encoding="utf-8")
        # the guard must open a statement — the same comparison inside a
        # template literal is a mention, not the branch that draws the scene
        opener = re.compile(r"^\s*\}?\s*(?:else\s+)?if\s*[\(\s]")
        marks = []
        for other in all_ids:
            for m in re.finditer(template.format(id=re.escape(other)), text):
                line_start = text.rfind("\n", 0, m.start()) + 1
                line_end = text.find("\n", m.start())
                line = text[line_start: len(text) if line_end < 0 else line_end]
                if opener.match(line):
                    marks.append((line_start, other))
                    break
        marks.sort()
        # a dispatch chain may end in a bare `else` — that default branch is
        # the one scene with no guard of its own
        unguarded = [i for i in all_ids if i not in {n for _, n in marks}]
        if marks and len(unguarded) == 1:
            tail = re.search(r"^\s*\}?\s*else(?!\s*if\b)\s*\{", text[marks[-1][0]:], re.M)
            if tail:
                marks.append((marks[-1][0] + tail.start(), unguarded[0]))
                marks.sort()
        for i, (start, name) in enumerate(marks):
            if name != demo_id:
                continue
            end = marks[i + 1][0] if i + 1 < len(marks) else len(text)
            body = text[start:end].rstrip().splitlines()
            if len(body) > MAX_BLOCK_LINES:
                body = body[:MAX_BLOCK_LINES] + [f"... ({len(body) - MAX_BLOCK_LINES} more lines)"]
            return "\n".join(body) + "\n", path.relative_to(ROOT).as_posix()
    return None


def extract_symbol(cfg: dict, demo_id: str) -> tuple[str, str] | None:
    """Pull one demo's own class/def out of a shared module.

    Some repos register every demo in a single file, so the card should show
    that demo's block rather than the whole module twelve times over.
    """
    glob = cfg.get("code_search")
    if not glob:
        return None
    words = re.split(r"[-_]", demo_id)
    camel = "".join(w.capitalize() for w in words)
    lower_camel = words[0] + "".join(w.capitalize() for w in words[1:])
    snake = demo_id.replace("-", "_")
    prefix = cfg.get("code_symbol_prefix", "")
    names = (prefix + snake, prefix + camel, prefix + lower_camel,
             camel, lower_camel, snake, demo_id)
    # top-level definition in any of the languages these repos use
    head = re.compile(
        r"^(?:export\s+)?(?:default\s+)?(?:async\s+)?"
        r"(?:function|const|let|var|class|def|struct|module|macro)\s+(\w+)",
        re.M,
    )
    files = sorted(ROOT.glob(glob), key=lambda p: natural_key(p.name))

    def block_at(text: str, marks: list, index: int) -> str:
        end = marks[index + 1][0] if index + 1 < len(marks) else len(text)
        return text[marks[index][0]:end].rstrip() + "\n"

    fallback = None
    for path in files:
        text = path.read_text(encoding="utf-8")
        marks = [(m.start(), m.group(1)) for m in head.finditer(text)]
        for i, (_, name) in enumerate(marks):
            if name in names:
                return block_at(text, marks, i), path.relative_to(ROOT).as_posix()
        # Some repos name the renderer independently of the demo id. Fall back
        # to the top-level block that mentions the id, preferring one whose
        # name carries the configured prefix over the registration block.
        if fallback is None:
            for m in re.finditer(re.escape(demo_id), text):
                owner = [i for i, (start, _) in enumerate(marks) if start <= m.start()]
                if not owner:
                    continue
                i = owner[-1]
                if prefix and not marks[i][1].startswith(prefix):
                    continue
                fallback = (block_at(text, marks, i), path.relative_to(ROOT).as_posix())
                break
    return fallback


def discover_sources(cfg: dict) -> list[Path]:
    found: list[Path] = []
    for pattern in cfg["sources"] if isinstance(cfg.get("sources"), list) else [cfg["sources"]]:
        found.extend(ROOT.glob(pattern))
    return sorted({p for p in found}, key=lambda p: natural_key(p.name))


def source_stem(path: Path, cfg: dict) -> str:
    if path.is_dir():
        return path.name
    name = path.name
    for suffix in cfg.get("strip_suffixes", []):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return path.stem


def artifacts_for(stem: str, cfg: dict) -> list[Path]:
    num = re.match(r"(\d{2,})[-_]", stem)
    prefix = cfg["artifact_prefix"].format(stem=stem, num=num.group(1) if num else stem)
    # artifact_dirs is a priority list: a gallery-sized thumbnail wins, but a
    # demo that never got one still shows its full-size render.
    drop = re.compile(cfg["artifact_exclude"]) if cfg["artifact_exclude"] else None
    hits: list[Path] = []
    for d in cfg["artifact_dirs"]:
        directory = ROOT / d
        if not directory.is_dir():
            continue
        for p in directory.rglob("*"):
            if not p.is_file() or p.suffix.lower() not in RENDERABLE:
                continue
            name = p.stem
            if drop and drop.search(name):
                continue
            if name == prefix or name.startswith(prefix + "-") or name.startswith(prefix + "."):
                hits.append(p)
        if hits:
            break
    order = {ext: i for i, ext in enumerate(cfg["prefer"])}
    hits.sort(key=lambda p: (
        0 if p.relative_to(ROOT).as_posix() in TRACKED else 1,
        order.get(p.suffix.lstrip(".").lower(), 99),
        natural_key(p.name),
    ))
    if not hits:
        return []
    best = hits[0].suffix.lower()
    # one format per demo: extra formats of the same picture are noise here
    chosen = [p for p in hits if p.suffix.lower() == best]
    if best in VIDEO_EXT:
        # keep a still of the same name as the poster frame
        stills = [p for p in hits if p.suffix.lower() in IMAGE_EXT]
        return [(v, next((s for s in stills if s.stem.startswith(v.stem)), None)) for v in chosen]
    return [(p, None) for p in chosen]


def capped(artifacts: list) -> tuple[list, int]:
    """Show at most a handful of views per card; a workspace that exports ten
    diagrams should not turn its card into a scrolling column."""
    return artifacts[:MAX_ARTIFACTS], max(0, len(artifacts) - MAX_ARTIFACTS)


def primary_source_file(path: Path, cfg: dict) -> Path | None:
    if path.is_file():
        return path
    for candidate in cfg.get("dir_entry", ["index.html", "spec.json", "main.ts"]):
        p = path / candidate
        if p.exists():
            return p
    files = sorted((p for p in path.iterdir() if p.is_file()), key=lambda p: natural_key(p.name))
    return files[0] if files else None


# --------------------------------------------------------------------------
# source highlighting — tokenize the RAW text, escape each span afterwards


COMMENT_RULES = {
    "#": {".py", ".gp", ".dbml", ".d2", ".c4", ".dsl", ".typ", ".jl", ".toml", ".yaml", ".yml"},
    "//": {".ts", ".js", ".mjs", ".cjs", ".json5", ".c4", ".dsl", ".d2", ".java"},
    "%": {".mp", ".tex"},
}


def highlight(text: str, suffix: str) -> str:
    """Escape to HTML, marking comments, strings and numbers. Never emits raw
    source into markup — every span is escaped at emit time."""
    line_comments = [tok for tok, exts in COMMENT_RULES.items() if suffix in exts]
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        matched = next((tok for tok in line_comments if text.startswith(tok, i)), None)
        if matched:
            end = text.find("\n", i)
            end = n if end < 0 else end
            out.append(f'<span class="c">{html.escape(text[i:end])}</span>')
            i = end
            continue
        if ch in "\"'":
            j = i + 1
            while j < n and text[j] != ch:
                j += 2 if text[j] == "\\" else 1
            j = min(j + 1, n)
            out.append(f'<span class="s">{html.escape(text[i:j])}</span>')
            i = j
            continue
        if ch.isdigit() and (i == 0 or not (text[i - 1].isalnum() or text[i - 1] == "_")):
            j = i
            while j < n and (text[j].isdigit() or text[j] == "."):
                j += 1
            out.append(f'<span class="n">{html.escape(text[i:j])}</span>')
            i = j
            continue
        j = i
        while j < n and text[j] not in "\"'#/%0123456789":
            j += 1
        j = max(j, i + 1)
        out.append(html.escape(text[i:j]))
        i = j
    return "".join(out)


# --------------------------------------------------------------------------
# page

CSS = """
:root{
  --bg:#fbfaf8; --panel:#fff; --ink:#16181d; --muted:#5f6570; --faint:#8b929e;
  --line:#e3e1dc; --accent:#2f6f5e; --chip:#eef1ef; --code:#f6f5f2;
  --shadow:0 1px 2px rgba(20,22,26,.05),0 8px 24px -12px rgba(20,22,26,.18);
}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){
  --bg:#0f1114; --panel:#161a1f; --ink:#e8eaee; --muted:#a2abb8; --faint:#727d8c;
  --line:#262c34; --accent:#6fd3b0; --chip:#1d232a; --code:#12161a;
  --shadow:0 1px 2px rgba(0,0,0,.4),0 10px 30px -14px rgba(0,0,0,.7);
}}
:root[data-theme=dark]{
  --bg:#0f1114; --panel:#161a1f; --ink:#e8eaee; --muted:#a2abb8; --faint:#727d8c;
  --line:#262c34; --accent:#6fd3b0; --chip:#1d232a; --code:#12161a;
  --shadow:0 1px 2px rgba(0,0,0,.4),0 10px 30px -14px rgba(0,0,0,.7);
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
  font:15px/1.6 ui-sans-serif,-apple-system,"Segoe UI",Inter,system-ui,sans-serif;
  -webkit-font-smoothing:antialiased}
.wrap{max-width:1240px;margin:0 auto;padding:56px 28px 96px}
header{border-bottom:1px solid var(--line);padding-bottom:28px;margin-bottom:40px}
h1{margin:0;font-size:30px;letter-spacing:-.02em;font-weight:650}
.tagline{margin:10px 0 0;color:var(--muted);max-width:70ch;font-size:16px}
.meta{margin-top:20px;display:flex;flex-wrap:wrap;gap:10px;align-items:center}
.pill{font:12px/1 ui-monospace,SFMono-Regular,Menlo,monospace;color:var(--muted);
  background:var(--chip);border:1px solid var(--line);border-radius:999px;padding:6px 11px}
.pill b{color:var(--ink);font-weight:600}
.controls{position:sticky;top:0;z-index:5;display:flex;flex-wrap:wrap;gap:10px;align-items:center;
  padding:12px 0;margin:0 0 26px;background:linear-gradient(var(--bg) 80%,transparent)}
.controls input{flex:1 1 220px;max-width:400px;border:1px solid var(--line);border-radius:10px;
  background:var(--panel);color:var(--ink);padding:9px 13px;font-size:14px;font-family:inherit}
.controls input:focus-visible,.chip:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.chips{display:flex;flex-wrap:wrap;gap:6px}
.chip{font:12px/1 ui-monospace,SFMono-Regular,Menlo,monospace;color:var(--muted);cursor:pointer;
  background:var(--chip);border:1px solid var(--line);border-radius:999px;padding:6px 11px}
.chip span{color:var(--faint);margin-left:3px}
.chip[aria-pressed="true"]{background:var(--accent);border-color:var(--accent);color:var(--bg)}
.chip[aria-pressed="true"] span{color:var(--bg);opacity:.75}
#count{margin-left:auto;color:var(--muted);font:12px/1 ui-monospace,SFMono-Regular,Menlo,monospace;white-space:nowrap}
#empty{color:var(--muted);font-size:14px}
.grid{display:grid;gap:26px;grid-template-columns:repeat(auto-fill,minmax(360px,1fr))}
.card{background:var(--panel);border:1px solid var(--line);border-radius:14px;
  overflow:hidden;box-shadow:var(--shadow);display:flex;flex-direction:column}
.shot{margin:0;padding:16px;display:flex;flex-direction:column;gap:10px;
  align-items:center;justify-content:center;height:250px;border-bottom:1px solid var(--line)}
.shot.tall{height:400px}
.shot.checker{background-color:#f2f1ee;background-image:
  linear-gradient(45deg,#e2e0dc 25%,transparent 25%,transparent 75%,#e2e0dc 75%),
  linear-gradient(45deg,#e2e0dc 25%,transparent 25%,transparent 75%,#e2e0dc 75%);
  background-size:16px 16px;background-position:0 0,8px 8px}
.shot.dark{background:#11151a}
.shot.light{background:#fff}
.shot img,.shot video{max-width:100%;max-height:100%;width:auto;height:auto;
  object-fit:contain;display:block;border-radius:3px}
.shot.multi img{max-height:calc(50% - 5px)}
.shot.multi{display:grid;grid-template-columns:1fr 1fr;place-items:center;gap:8px}
.shot.multi img{max-height:100%;max-width:100%}
.shot .missing{color:var(--faint);font-size:13px;font-style:italic;text-align:center}
.body{padding:18px 20px 20px;display:flex;flex-direction:column;gap:10px;flex:1}
.idx{font:11px/1 ui-monospace,SFMono-Regular,Menlo,monospace;color:var(--faint);
  letter-spacing:.09em;text-transform:uppercase}
h2{margin:0;font-size:17px;font-weight:620;letter-spacing:-.01em}
.blurb{margin:0;color:var(--muted);font-size:14px}
.tags{display:flex;flex-wrap:wrap;gap:6px;margin-top:2px}
.tag{font:11px/1 ui-monospace,SFMono-Regular,Menlo,monospace;color:var(--muted);
  background:var(--chip);border-radius:5px;padding:4px 7px}
details{margin-top:auto;border-top:1px solid var(--line);padding-top:12px}
summary{cursor:pointer;font:12px/1 ui-monospace,SFMono-Regular,Menlo,monospace;
  color:var(--accent);list-style:none;user-select:none}
summary::-webkit-details-marker{display:none}
summary::before{content:"▸ ";display:inline-block;transition:transform .15s}
details[open] summary::before{content:"▾ "}
pre{margin:12px 0 0;background:var(--code);border:1px solid var(--line);border-radius:8px;
  padding:14px;overflow-x:auto;font:12px/1.65 ui-monospace,SFMono-Regular,Menlo,monospace;
  max-height:440px;overflow-y:auto}
pre .c{color:var(--faint);font-style:italic}
pre .s{color:var(--accent)}
pre .n{color:#b1784f}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]) pre .n{color:#d8a06a}}
:root[data-theme=dark] pre .n{color:#d8a06a}
.extra{font:11px/1 ui-monospace,SFMono-Regular,Menlo,monospace;color:var(--faint)}
footer{margin-top:56px;padding-top:22px;border-top:1px solid var(--line);
  color:var(--faint);font-size:13px}
footer code{background:var(--chip);border-radius:4px;padding:2px 6px;
  font:12px/1 ui-monospace,SFMono-Regular,Menlo,monospace}
"""


def index_line(ordinal: int, stem: str, entry: dict) -> str:
    """`03 · phase portrait` — the position plus the demo's family, never a
    restatement of the title right below it."""
    lead = re.match(r"(\d{2,})[-_]", stem)
    left = lead.group(1) if lead else f"{ordinal:02d}"
    right = entry.get("family") or entry.get("use") or ""
    return f"{left} · {right}" if right else left


def render_card(item: dict, cfg: dict) -> str:
    shown, hidden = capped(item["artifacts"])
    shot = []
    for art, poster in shown:
        rel = html.escape(art.relative_to(ROOT).as_posix())
        if art.suffix.lower() in VIDEO_EXT:
            frame = f' poster="{html.escape(poster.relative_to(ROOT).as_posix())}"' if poster else ""
            shot.append(f'<video src="{rel}"{frame} controls muted loop playsinline preload="none"></video>')
        else:
            shot.append(f'<img src="{rel}" alt="{html.escape(item["title"])}" loading="lazy">')
    if not shot:
        shot.append('<p class="missing">not rendered yet — run the build</p>')

    tags = "".join(f'<span class="tag">{html.escape(t)}</span>' for t in item["tags"])
    blurb = f'<p class="blurb">{html.escape(item["blurb"])}</p>' if item["blurb"] else ""
    tagsrow = f'<div class="tags">{tags}</div>' if tags else ""

    src = ""
    if item["code"] is not None:
        rel = html.escape(item["code_path"])
        extra = ""
        if len(item["artifacts"]) > 1:
            extra = f'<span class="extra"> · {len(item["artifacts"])} views</span>'
        src = (
            f'<details><summary>{rel}{extra}</summary>'
            f'<pre><code>{item["code"]}</code></pre></details>'
        )

    klass = f"shot {cfg['backdrop']}"
    if len(shown) > 1:
        klass += " multi tall"
    family = html.escape(item["family"], quote=True)
    search = html.escape(
        " ".join((item["title"], item["blurb"], " ".join(item["tags"]), item["index"], item["family"])).casefold(),
        quote=True,
    )
    return f"""      <article class="card" data-family="{family}" data-search="{search}">
        <figure class="{klass}">{''.join(shot)}</figure>
        <div class="body">
          <div class="idx">{html.escape(item['index'])}</div>
          <h2>{html.escape(item['title'])}</h2>
          {blurb}
          {tagsrow}
          {src}
        </div>
      </article>"""


def build(check_only: bool = False) -> int:
    global TRACKED
    TRACKED = tracked_files()
    cfg = load_config()
    catalog = load_catalog()
    blurbs = load_readme_blurbs()

    paths = discover_sources(cfg) if cfg.get("sources") else [None] * len(catalog)
    ids = list(catalog) if not cfg.get("sources") else []

    items = []
    for pos, path in enumerate(paths):
        stem = source_stem(path, cfg) if path is not None else ids[pos]
        entry = catalog.get(stem) or catalog.get(re.sub(r"^\d+[-_]", "", stem)) or {}
        rel_src = path.relative_to(ROOT).as_posix() if path is not None else ""

        title = entry.get("title") or cfg.get("titles", {}).get(stem)
        if not title:
            title = re.sub(r"^\d+[-_]", "", stem).replace("-", " ").replace("_", " ").strip()
            title = title[:1].upper() + title[1:]

        blurb = (
            entry.get("question")
            or entry.get("description")
            or blurbs.get(rel_src)
            or blurbs.get(rel_src.rstrip("/"))
            or ""
        )
        blurb = re.sub(r"`([^`]*)`", r"\1", re.sub(r"\*\*([^*]*)\*\*", r"\1", blurb))

        tags = list(dict.fromkeys(entry.get("tags", [])))
        if entry.get("complexity") and entry["complexity"] not in tags:
            tags.append(entry["complexity"])

        code = code_path = None
        symbol = None
        if path is None:
            symbol = extract_guarded(cfg, stem, list(catalog)) or extract_symbol(cfg, stem)
        if symbol is not None:
            body, code_path = symbol
            code = highlight(body, Path(code_path).suffix.lower())
        else:
            code_file = primary_source_file(path, cfg) if path is not None else None
            if code_file and code_file.stat().st_size <= 200_000:
                try:
                    code = highlight(code_file.read_text(encoding="utf-8"), code_file.suffix.lower())
                    code_path = code_file.relative_to(ROOT).as_posix()
                except UnicodeDecodeError:
                    code = None

        items.append(
            {
                "index": index_line(len(items) + 1, stem, entry),
                "title": title,
                "blurb": blurb,
                "tags": tags,
                "family": (entry.get("family") or entry.get("use") or "").strip(),
                "artifacts": artifacts_for(stem, cfg),
                "code": code,
                "code_path": code_path,
            }
        )

    rendered = sum(1 for i in items if i["artifacts"])
    uncommitted = sum(
        1 for i in items for a, _ in i["artifacts"]
        if TRACKED and a.relative_to(ROOT).as_posix() not in TRACKED
    )
    cards = "\n".join(render_card(i, cfg) for i in items)
    pills = [
        f'<span class="pill"><b>{len(items)}</b> demos</span>',
        f'<span class="pill"><b>{rendered}</b> rendered</span>',
    ]
    if cfg.get("library"):
        pills.insert(0, f'<span class="pill">{html.escape(cfg["library"])}</span>')

    # Family chips only where family is a real grouping axis: the number of
    # distinct families must be at most half the demos (grouping actually
    # condenses). Where nearly every demo is its own family, chips would be
    # fake navigation — search alone.
    groups: dict[str, int] = {}
    for i in items:
        if i["family"]:
            groups[i["family"]] = groups.get(i["family"], 0) + 1
    ordered = sorted(groups.items(), key=lambda kv: (-kv[1], kv[0].casefold()))
    if len(ordered) >= 2 and len(ordered) * 2 <= len(items):
        chip = ['<button class="chip" type="button" data-family="" aria-pressed="true">All</button>']
        chip += [
            f'<button class="chip" type="button" data-family="{html.escape(f, quote=True)}">'
            f'{html.escape(f)} <span>{n}</span></button>'
            for f, n in ordered
        ]
        chips_html = f'<div class="chips" role="group" aria-label="Filter by family">{"".join(chip)}</div>'
    else:
        chips_html = ""
    controls = f"""  <section class="controls" aria-label="Gallery filters">
    <input id="q" type="search" placeholder="Filter by title, blurb, tag, or family…" autocomplete="off" aria-label="Filter demos">
    {chips_html}
    <output id="count" aria-live="polite">{len(items)} shown</output>
  </section>
  <p id="empty" hidden>No demos match the current filter.</p>"""

    page = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(cfg['title'])} — gallery</title>
<style>{CSS}</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>{html.escape(cfg['title'])}</h1>
    <p class="tagline">{html.escape(cfg['tagline'])}</p>
    <div class="meta">{''.join(pills)}</div>
  </header>
{controls}
  <main class="grid">
{cards}
  </main>
  <footer>
    Generated by <code>tools/gallery.py</code> — regenerate with <code>just gallery</code>
    after <code>{html.escape(cfg['rebuild'])}</code>. Images are referenced from
    <code>{html.escape('/'.join(cfg['artifact_dirs']))}/</code>, so open this file straight from the checkout.
  </footer>
</div>
<script>
(function(){{
  var q=document.getElementById('q'),count=document.getElementById('count'),empty=document.getElementById('empty');
  var cards=[].slice.call(document.querySelectorAll('.card'));
  var chips=[].slice.call(document.querySelectorAll('.chip'));
  var family='';
  function apply(){{
    var t=q.value.trim().toLowerCase(),n=0;
    for(var i=0;i<cards.length;i++){{
      var c=cards[i];
      var ok=(!family||c.getAttribute('data-family')===family)&&
             (!t||(c.getAttribute('data-search')||'').indexOf(t)>=0);
      c.style.display=ok?'':'none';
      if(ok)n++;
    }}
    count.value=n+' / '+cards.length+' shown';
    empty.hidden=n>0;
    for(var j=0;j<chips.length;j++){{
      var ch=chips[j];
      ch.setAttribute('aria-pressed',String(ch.getAttribute('data-family')===family));
    }}
  }}
  q.addEventListener('input',apply);
  chips.forEach(function(ch){{
    ch.addEventListener('click',function(){{
      var f=ch.getAttribute('data-family');
      family=(f===family)?'':f;
      apply();
    }});
  }});
  apply();
}})();
</script>
</body>
</html>
"""
    if check_only:
        if not OUTPUT.exists() or OUTPUT.read_text(encoding="utf-8") != page:
            print("stale gallery: run just gallery", file=sys.stderr)
            return 1
        print(f"gallery.html: current ({len(items)} demos, {rendered} with artifacts)")
        return 0
    OUTPUT.write_text(page, encoding="utf-8")
    note = f", {uncommitted} artifacts not committed (blank in a fresh clone)" if uncommitted else ""
    print(f"gallery.html: {len(items)} demos, {rendered} with artifacts{note}")
    return 0 if rendered else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true",
                        help="fail (exit 1) unless gallery.html is current")
    args = parser.parse_args()
    sys.exit(build(check_only=args.check))
