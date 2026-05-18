"""L4 会话日志压缩与归档。

移植自 GenericAgent 的 compress_session.py，适配 Chrysalis 路径结构。
原始日志：data/model_responses/
压缩归档：data/l4_session/
历史合并：data/l4_session/all_histories.txt
"""

import os
import re
import shutil
import tempfile
import time
import zipfile
from collections import defaultdict
from datetime import datetime
from glob import glob
from pathlib import Path

from configs.config import PROJECT_ROOT

RAW_DIR = PROJECT_ROOT / "data" / "model_responses"
L4_DIR = PROJECT_ROOT / "data" / "l4_session"

_RE_PROMPT = re.compile(r"^=== Prompt ===(?: (\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}))?", re.M)
_RE_RESPONSE = re.compile(r"^=== Response ===(?: (\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}))?", re.M)
_RE_USER = re.compile(r"^=== USER ===$", re.M)
_RE_ASST = re.compile(r"^=== ASSISTANT ===$", re.M)
_RE_ANY_MARKER = re.compile(r"^=== (?:Prompt|Response|USER|ASSISTANT|SYSTEM) ===(?:.*)?$", re.M)
_RE_HISTORY = re.compile(r"<history>(.*?)</history>", re.S)


def _ts_fmt(ts_str: str) -> str | None:
    try:
        return datetime.strptime(ts_str.strip(), "%Y-%m-%d %H:%M:%S").strftime("%m%d_%H%M")
    except Exception:
        return None


def _parse_sections(text: str) -> list[tuple]:
    _MAP = {"Prompt": "prompt", "Response": "response", "USER": "user",
             "ASSISTANT": "assistant", "SYSTEM": "system"}
    markers = list(_RE_ANY_MARKER.finditer(text))
    if not markers:
        return [("preamble", "", text)]
    sections = []
    if markers[0].start() > 0:
        sections.append(("preamble", "", text[: markers[0].start()]))
    for i, m in enumerate(markers):
        line = m.group()
        end = markers[i + 1].start() if i + 1 < len(markers) else len(text)
        typ = next((v for k, v in _MAP.items() if line.startswith(f"=== {k}")), None)
        if typ:
            sections.append((typ, line, text[m.end(): end]))
    return sections


def _compress_raw(text: str) -> str:
    sections = _parse_sections(text)
    out = []
    for i, (typ, line, body) in enumerate(sections):
        if typ == "prompt":
            out.append(line + "\n")
            if not (i + 1 < len(sections) and sections[i + 1][0] in ("user", "system")):
                out.append(body)
        elif typ in ("user", "response"):
            out.append(line + "\n")
            out.append(body)
        elif typ == "preamble":
            out.append(body)
    return "".join(out)


def compress_session(src: str, dst_dir: str | None = None) -> tuple:
    dst_dir = dst_dir or str(L4_DIR)
    os.makedirs(dst_dir, exist_ok=True)
    with open(src, "r", encoding="utf-8", errors="replace") as f:
        text = f.read()
    timestamps = [m.group(1) for m in _RE_PROMPT.finditer(text) if m.group(1)]
    if not timestamps:
        timestamps = [m.group(1) for m in _RE_RESPONSE.finditer(text) if m.group(1)]
    if not timestamps:
        return None, "no timestamps found"
    ts_first, ts_last = _ts_fmt(timestamps[0]), _ts_fmt(timestamps[-1])
    if not ts_first:
        return None, "bad timestamp format"
    name = f"{ts_first}-{ts_last or ts_first}.txt"
    compressed = _compress_raw(text)
    if len(compressed.encode("utf-8")) < 4500:
        return None, f"too small after compress ({len(compressed)}B)"
    dst = os.path.join(dst_dir, name)
    with open(dst, "w", encoding="utf-8", newline="") as f:
        f.write(compressed)
    orig_kb = os.path.getsize(src) // 1024
    new_kb = os.path.getsize(dst) // 1024
    ratio = (1 - new_kb / max(orig_kb, 1)) * 100
    return dst, {
        "src": os.path.basename(src), "dst": name,
        "orig_kb": orig_kb, "new_kb": new_kb, "ratio": f"{ratio:.0f}%",
        "year": timestamps[0][:4],
    }


def _parse_history_block(raw: str) -> list[str]:
    lines = [line.strip() for line in raw.split("\n") if line.strip()]
    parsed = [line for line in lines if line.startswith("[USER]") or line.startswith("[Agent]")]
    if len(parsed) >= 2:
        return parsed
    joined = raw.strip()
    if "\\n[USER]" in joined or "\\n[Agent]" in joined:
        parts = joined.replace("\\n", "\n").split("\n")
        parsed = [p.strip() for p in parts
                  if p.strip() and (p.strip().startswith("[USER]") or p.strip().startswith("[Agent]"))]
        if parsed:
            return parsed
    return parsed or []


def _merge_history_blocks(all_blocks: list[list[str]]) -> list[str]:
    if not all_blocks:
        return []
    acc = list(all_blocks[0])
    for block in all_blocks[1:]:
        if not block:
            continue
        if not acc:
            acc = list(block)
            continue
        best = 0
        for k in range(1, min(len(acc), len(block)) + 1):
            if acc[-k:] == block[:k]:
                best = k
        if best > 0:
            acc.extend(block[best:])
        elif block[0] in acc:
            idx = len(acc) - 1 - acc[::-1].index(block[0])
            match_len = 0
            for j in range(min(len(block), len(acc) - idx)):
                if acc[idx + j] == block[j]:
                    match_len = j + 1
                else:
                    break
            acc.extend(block[match_len:])
        else:
            acc.extend(block)
    return acc


def extract_history(src: str) -> list[str]:
    with open(src, "r", encoding="utf-8", errors="replace") as f:
        text = f.read()
    all_blocks = [parsed for m in _RE_HISTORY.finditer(text)
                  if (parsed := _parse_history_block(m.group(1)))]
    if all_blocks:
        return _merge_history_blocks(all_blocks)
    return []


def format_history_block(session_name: str, history_lines: list[str]) -> str:
    sep = "=" * 60
    return f"{sep}\nSESSION: {session_name}\n{sep}\n" + "\n".join(history_lines) + "\n"


def _existing_sessions(l4_dir: str) -> set[str]:
    hist_path = os.path.join(l4_dir, "all_histories.txt")
    if not os.path.exists(hist_path):
        return set()
    with open(hist_path, "r", encoding="utf-8") as f:
        return {line.strip().replace("SESSION: ", "") for line in f if line.startswith("SESSION: ")}


def batch_process(src=None, l4_dir: str | None = None, dry_run: bool = True) -> dict:
    l4_dir = os.path.normpath(l4_dir or str(L4_DIR))
    os.makedirs(l4_dir, exist_ok=True)

    if src is None:
        src = str(RAW_DIR)
    raw_files = (sorted(src) if isinstance(src, (list, tuple))
                 else sorted(glob(os.path.join(src, "model_responses_*.txt"))))
    if not raw_files:
        print("No raw files found")
        return {"processed": 0, "skipped": 0, "errors": 0, "new_sessions": 0}

    existing = _existing_sessions(l4_dir)
    print(f"Found {len(raw_files)} raw, {len(existing)} existing in L4")

    tmp_dir = tempfile.mkdtemp(prefix="cs_batch_")
    results, skipped, errors = [], [], []
    cutoff = time.time() - 7200

    for fp in raw_files:
        fname = os.path.basename(fp)
        if os.path.getmtime(fp) > cutoff:
            skipped.append((fname, "recent(<2h)"))
            continue
        try:
            dst, info = compress_session(fp, tmp_dir)
            if dst is None:
                skipped.append((fname, info))
                continue
            sn = os.path.splitext(os.path.basename(dst))[0]
            if sn in existing:
                skipped.append((fname, f"dup:{sn}"))
                os.remove(dst)
                continue
            results.append((sn, dst, extract_history(dst), info, fp))
        except Exception as e:
            errors.append((fname, str(e)))
    results.sort(key=lambda x: x[0])

    print(f"\nPhase 1: {len(results)} new, {len(skipped)} skip, {len(errors)} err")
    for f, r in skipped[:5]:
        print(f"  SKIP {f}: {r}")
    for f, e in errors[:5]:
        print(f"  ERR  {f}: {e}")

    if dry_run:
        print("\n[DRY RUN] Pass dry_run=False to execute.")
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return {"processed": len(results), "skipped": len(skipped),
                "errors": len(errors), "new_sessions": len(results)}

    with open(os.path.join(l4_dir, "all_histories.txt"), "a", encoding="utf-8") as f:
        for sn, _, hist, _, _ in results:
            if hist:
                f.write("\n" + format_history_block(sn, hist))
    print(f"Appended {len(results)} sessions to all_histories.txt")

    by_month: dict[str, list] = defaultdict(list)
    for sn, cpath, _, info, _ in results:
        year = info.get("year", "2026") if isinstance(info, dict) else "2026"
        by_month[f"{year}-{sn[:2]}"].append((sn, cpath))
    for mk, items in sorted(by_month.items()):
        zpath = os.path.join(l4_dir, f"{mk}.zip")
        mode = "a" if os.path.exists(zpath) else "w"
        with zipfile.ZipFile(zpath, mode, zipfile.ZIP_DEFLATED) as zf:
            names = set(zf.namelist()) if mode == "a" else set()
            for sn, cp in items:
                if f"{sn}.txt" not in names:
                    zf.write(cp, f"{sn}.txt")
        print(f"  {mk}.zip: +{len(items)}")

    to_del = [rp for *_, rp in results]
    deleted = 0
    for rp in to_del:
        try:
            os.remove(rp)
            deleted += 1
        except Exception:
            pass
    print(f"Deleted {deleted}/{len(to_del)} raw files")

    shutil.rmtree(tmp_dir, ignore_errors=True)
    report = {"processed": len(results), "skipped": len(skipped),
              "errors": len(errors), "new_sessions": len(results), "deleted_raw": deleted}
    print(f"\nDone: {report}")
    return report


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Chrysalis L4 session archiver")
    ap.add_argument("src", nargs="?", default=str(RAW_DIR), help="raw files dir")
    ap.add_argument("--run", action="store_true", help="actually execute (default: dry run)")
    args = ap.parse_args()
    batch_process(args.src, dry_run=not args.run)
