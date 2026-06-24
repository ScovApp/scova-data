#!/usr/bin/env python3
"""
Apply a safe Scova contests.json update.

This script is an orchestrator, not a replacement for the canonical validator.
It reads the current Scova JSON, applies verified candidate contests, rebuilds
sections that are computed daily, then calls tools/validate_contests.py.

Typical local use:
  python tools/apply_contests_update.py \
    --in contests.json \
    --candidate-file tmp/new-contests.json \
    --today 2026-06-24 \
    --updated-at 2026-06-24T08:30:00+02:00 \
    --out contests.updated.json

Optional GitHub upload, only for branch test:
  GITHUB_TOKEN=... python tools/apply_contests_update.py \
    --github-upload \
    --repo ScovApp/scova-data \
    --branch test \
    --path contests.json \
    --candidate-file tmp/new-contests.json \
    --today 2026-06-24 \
    --updated-at 2026-06-24T08:30:00+02:00
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

RAW_TEST_URL = "https://raw.githubusercontent.com/ScovApp/scova-data/refs/heads/test/contests.json"
DEFAULT_VALIDATOR = "tools/validate_contests.py"
REQUIRED_SECTIONS = ["hero_main", "featured", "new", "expiring_soon", "all"]


def load_json_file(path: str | Path) -> Any:
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def dump_json_text(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def read_url(url: str, token: str | None = None) -> bytes:
    headers = {"User-Agent": "scova-contests-updater/1.0"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
        headers["Accept"] = "application/vnd.github+json"
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read()


def load_base(args: argparse.Namespace) -> tuple[dict[str, Any], str | None]:
    if args.inp:
        return load_json_file(args.inp), None

    if args.github_upload or args.from_raw:
        raw = read_url(args.raw_url).decode("utf-8")
        return json.loads(raw), None

    raise SystemExit("Provide --in, --from-raw, or --github-upload")


def find_home_sections(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    pages = payload.get("pages")
    if not isinstance(pages, list):
        raise ValueError("root.pages must be a list")

    home = next((page for page in pages if isinstance(page, dict) and page.get("id") == "home"), None)
    if not home:
        raise ValueError("missing page with id 'home'")

    sections = home.get("sections")
    if not isinstance(sections, list):
        raise ValueError("home.sections must be a list")

    by_id = {section.get("id"): section for section in sections if isinstance(section, dict)}
    missing = [section_id for section_id in REQUIRED_SECTIONS if section_id not in by_id]
    if missing:
        raise ValueError(f"missing required sections: {', '.join(missing)}")

    return by_id


def normalize_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    # Keep unknown extra fields, but guarantee image fallback compatibility fields exist.
    normalized = deepcopy(candidate)
    fallback = normalized.get("imageUrl") or normalized.get("providerLogoUrl") or normalized.get("url")
    for field in ["cover", "coverUrl", "brandImage", "image", "providerLogoUrl", "imageUrl"]:
        if not normalized.get(field) and fallback:
            normalized[field] = fallback
    normalized.setdefault("ogImage", "")
    normalized.setdefault("twitterImage", "")
    return normalized


def load_candidates(args: argparse.Namespace) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []

    for candidate_file in args.candidate_file or []:
        loaded = load_json_file(candidate_file)
        if isinstance(loaded, list):
            candidates.extend(loaded)
        elif isinstance(loaded, dict):
            candidates.append(loaded)
        else:
            raise ValueError(f"candidate file {candidate_file} must contain an object or array")

    for inline in args.candidate_json or []:
        loaded = json.loads(inline)
        if isinstance(loaded, list):
            candidates.extend(loaded)
        elif isinstance(loaded, dict):
            candidates.append(loaded)
        else:
            raise ValueError("--candidate-json must be an object or array")

    normalized = [normalize_candidate(candidate) for candidate in candidates]
    for candidate in normalized:
        if not isinstance(candidate.get("id"), str) or not candidate["id"].strip():
            raise ValueError("all candidates must have a non-empty id")
    return normalized


def parse_date(value: str) -> datetime.date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def rebuild_expiring_soon(all_contests: list[dict[str, Any]], today: str) -> list[dict[str, Any]]:
    today_date = parse_date(today)
    max_date = today_date + timedelta(days=5)
    result: list[dict[str, Any]] = []

    for contest in all_contests:
        deadline = parse_date(str(contest.get("deadline", "")))
        if today_date <= deadline <= max_date:
            result.append(deepcopy(contest))

    return result


def apply_update(payload: dict[str, Any], candidates: list[dict[str, Any]], today: str, updated_at: str) -> tuple[dict[str, Any], dict[str, Any]]:
    updated = deepcopy(payload)
    updated["updatedAt"] = updated_at
    sections = find_home_sections(updated)

    all_contests = sections["all"].get("contests")
    if not isinstance(all_contests, list):
        raise ValueError("section all.contests must be a list")

    before_count = len(all_contests)
    existing_ids = {contest.get("id") for contest in all_contests if isinstance(contest, dict)}
    added: list[dict[str, Any]] = []
    skipped_existing: list[str] = []

    for candidate in candidates:
        cid = candidate["id"]
        if cid in existing_ids:
            skipped_existing.append(cid)
            continue
        all_contests.append(deepcopy(candidate))
        existing_ids.add(cid)
        added.append(deepcopy(candidate))

    # Daily computed sections.
    sections["new"]["contests"] = added
    sections["expiring_soon"]["contests"] = rebuild_expiring_soon(all_contests, today)

    after_count = len(all_contests)
    if after_count < before_count:
        raise ValueError(f"anomalous reduction: all went from {before_count} to {after_count}")

    all_ids = [contest.get("id") for contest in all_contests if isinstance(contest, dict)]
    duplicates = sorted({cid for cid in all_ids if all_ids.count(cid) > 1})
    if duplicates:
        raise ValueError(f"duplicate ids in all: {', '.join(duplicates)}")

    report = {
        "before_all": before_count,
        "after_all": after_count,
        "added_ids": [contest["id"] for contest in added],
        "skipped_existing_ids": skipped_existing,
        "new_count": len(sections["new"].get("contests", [])),
        "expiring_soon_count": len(sections["expiring_soon"].get("contests", [])),
    }
    return updated, report


def run_validator(json_path: str, validator_path: str, today: str, strict_categories: bool = False) -> None:
    command = [sys.executable, validator_path, "--in", json_path, "--today", today]
    if strict_categories:
        command.append("--strict-categories")

    completed = subprocess.run(command, text=True, capture_output=True)
    if completed.stdout:
        print(completed.stdout.strip())
    if completed.stderr:
        print(completed.stderr.strip(), file=sys.stderr)
    if completed.returncode != 0:
        raise SystemExit(f"validator failed with exit code {completed.returncode}")


def github_get_file_sha(repo: str, branch: str, path: str, token: str) -> str:
    url = f"https://api.github.com/repos/{repo}/contents/{path}?ref={branch}"
    payload = json.loads(read_url(url, token=token).decode("utf-8"))
    sha = payload.get("sha")
    if not sha:
        raise RuntimeError("GitHub contents response did not include sha")
    return sha


def github_upload(repo: str, branch: str, path: str, content: str, message: str, token: str) -> dict[str, Any]:
    sha = github_get_file_sha(repo, branch, path, token)
    body = {
        "message": message,
        "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
        "sha": sha,
        "branch": branch,
    }
    request = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/contents/{path}",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "User-Agent": "scova-contests-updater/1.0",
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
        },
        method="PUT",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"GitHub upload failed: HTTP {exc.code}: {detail}") from exc


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--in", dest="inp", default="", help="Local input contests.json")
    parser.add_argument("--out", default="", help="Local output contests.json")
    parser.add_argument("--candidate-file", action="append", default=[], help="Candidate contest JSON object or array")
    parser.add_argument("--candidate-json", action="append", default=[], help="Inline candidate contest JSON object or array")
    parser.add_argument("--today", required=True, help="YYYY-MM-DD date used for deadline and expiring_soon rules")
    parser.add_argument("--updated-at", required=True, help="updatedAt value to write")
    parser.add_argument("--validator", default=DEFAULT_VALIDATOR, help="Path to existing tools/validate_contests.py")
    parser.add_argument("--strict-categories", action="store_true")
    parser.add_argument("--from-raw", action="store_true", help="Read base JSON from raw test URL")
    parser.add_argument("--raw-url", default=RAW_TEST_URL)
    parser.add_argument("--github-upload", action="store_true", help="Upload final JSON to GitHub after validation")
    parser.add_argument("--repo", default="ScovApp/scova-data")
    parser.add_argument("--branch", default="test")
    parser.add_argument("--path", default="contests.json")
    parser.add_argument("--message", default="Update Scova contests feed")
    args = parser.parse_args()

    if args.github_upload and args.branch != "test":
        raise SystemExit("Safety stop: this script may upload only to branch 'test'")

    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if args.github_upload and not token:
        raise SystemExit("Set GITHUB_TOKEN or GH_TOKEN for --github-upload")

    base, _ = load_base(args)
    candidates = load_candidates(args)
    updated, report = apply_update(base, candidates, args.today, args.updated_at)
    content = dump_json_text(updated)

    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json", delete=False) as handle:
        handle.write(content)
        temp_path = handle.name

    try:
        run_validator(temp_path, args.validator, args.today, strict_categories=args.strict_categories)
    finally:
        try:
            os.unlink(temp_path)
        except OSError:
            pass

    if args.out:
        Path(args.out).write_text(content, encoding="utf-8")

    upload_result = None
    if args.github_upload:
        upload_result = github_upload(args.repo, args.branch, args.path, content, args.message, token or "")

    print(json.dumps({"status": "ok", "report": report, "uploaded": bool(upload_result)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
