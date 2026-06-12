#!/usr/bin/env python3
import argparse
import copy
import json
import re
import sys
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta

try:
    from zoneinfo import ZoneInfo
except Exception:
    ZoneInfo = None


DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
HTTP_RE = re.compile(r"^https?://", re.IGNORECASE)

REQUIRED_ROOT_FIELDS = ["schemaVersion", "updatedAt", "pages"]
REQUIRED_PAGE_FIELDS = ["id", "name", "category", "sections"]
REQUIRED_SECTION_IDS = ["hero_main", "featured", "new", "expiring_soon", "all"]

EXPECTED_SECTION_TYPES = {
    "hero_main": "banner",
    "featured": "contest_carousel",
    "new": "contest_carousel",
    "expiring_soon": "contest_carousel",
    "all": "contest_grid",
}

REQUIRED_CONTEST_FIELDS = [
    "id",
    "title",
    "prize",
    "platform",
    "category",
    "deadline",
    "url",
    "imageUrl",
    "providerLogoUrl",
    "termsUrl",
    "approved",
]

REQUIRED_URL_FIELDS = ["url", "imageUrl", "providerLogoUrl", "termsUrl"]

OPTIONAL_IMAGE_FIELDS = [
    "cover",
    "coverUrl",
    "brandImage",
    "ogImage",
    "twitterImage",
    "image",
]

KNOWN_CATEGORIES = {
    "Tech",
    "Shopping",
    "Viaggi",
    "Musica",
    "Senza Acquisto",
    "Gare",
    "Giochi",
    "Benessere",
    "Sport",
    "Tempo Libero",
    "Gastronomia",
    "Moda",
    "Gratis",
    "Bevande",
    "Alimentari",
    "Food",
    "Food delivery",
    "Snack",
    "Supermercati",
    "Finanza",
    "Finanza e assicurazioni",
    "Energia",
    "Fai da te",
    "Casa e cura persona",
    "Salute e benessere",
    "Dolci e alimentari",
    "Gaming",
    "Cinema",
    "Centri commerciali",
    "Esperienze",
    "Loyalty",
}


def get_today(timezone, forced_today=None):
    if forced_today:
        return datetime.strptime(forced_today, "%Y-%m-%d").date()

    if ZoneInfo:
        return datetime.now(ZoneInfo(timezone)).date()

    return date.today()


def is_http_url(value):
    return isinstance(value, str) and bool(HTTP_RE.match(value.strip()))


def parse_deadline(value):
    if not isinstance(value, str) or not DATE_RE.match(value):
        return None

    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except Exception:
        return None


def http_status(url, timeout=12):
    headers = {"User-Agent": "scova-data-validator/2.0"}

    for method in ("HEAD", "GET"):
        req = urllib.request.Request(url, headers=headers, method=method)

        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                status = getattr(resp, "status", None) or resp.getcode()
                ctype = resp.headers.get("Content-Type")
                return status, ctype

        except urllib.error.HTTPError as e:
            if method == "HEAD" and e.code in (405, 403):
                continue

            ctype = e.headers.get("Content-Type") if hasattr(e, "headers") else None
            return e.code, ctype

        except Exception:
            if method == "HEAD":
                continue
            return None, None

    return None, None


def is_pdf(url, content_type):
    clean_url = url.lower().split("?")[0]

    if clean_url.endswith(".pdf"):
        return True

    if content_type and "application/pdf" in content_type.lower():
        return True

    return False


def contest_path(section_id, index):
    return f"{section_id}.contests[{index}]"


def extract_legacy_or_scova_data(raw_data):
    if isinstance(raw_data, list):
        return "legacy", raw_data, raw_data, {}

    if not isinstance(raw_data, dict):
        raise ValueError("contests.json root must be an object or an array")

    pages = raw_data.get("pages")
    if not isinstance(pages, list):
        raise ValueError("missing or invalid root.pages array")

    home = next(
        (page for page in pages if isinstance(page, dict) and page.get("id") == "home"),
        None,
    )

    if not home:
        raise ValueError("missing page with id 'home'")

    sections = home.get("sections")
    if not isinstance(sections, list):
        raise ValueError("missing or invalid home.sections array")

    sections_by_id = {}

    for index, section in enumerate(sections):
        if not isinstance(section, dict):
            raise ValueError(f"home.sections[{index}] must be an object")

        section_id = section.get("id")

        if not isinstance(section_id, str) or not section_id.strip():
            raise ValueError(f"home.sections[{index}] missing id")

        if section_id in sections_by_id:
            raise ValueError(f"duplicate section id '{section_id}'")

        sections_by_id[section_id] = section

    all_section = sections_by_id.get("all")

    if not all_section:
        raise ValueError("missing section with id 'all'")

    if all_section.get("type") != "contest_grid":
        raise ValueError("section 'all' must have type 'contest_grid'")

    contests = all_section.get("contests")

    if not isinstance(contests, list):
        raise ValueError("section 'all.contests' must be an array")

    return "scova", contests, raw_data, sections_by_id


def validate_scova_structure(payload, sections_by_id, errors):
    for field in REQUIRED_ROOT_FIELDS:
        if field not in payload:
            errors.append(f"root missing '{field}'")

    if "schemaVersion" in payload and not isinstance(payload["schemaVersion"], int):
        errors.append("schemaVersion must be an integer")

    if "updatedAt" in payload and not isinstance(payload["updatedAt"], str):
        errors.append("updatedAt must be a string")

    pages = payload.get("pages")

    if not isinstance(pages, list):
        errors.append("root.pages must be an array")
        return

    home_pages = [
        page for page in pages
        if isinstance(page, dict) and page.get("id") == "home"
    ]

    if len(home_pages) != 1:
        errors.append("there must be exactly one page with id 'home'")
        return

    home = home_pages[0]

    for field in REQUIRED_PAGE_FIELDS:
        if field not in home:
            errors.append(f"home page missing '{field}'")

    for section_id in REQUIRED_SECTION_IDS:
        if section_id not in sections_by_id:
            errors.append(f"missing section '{section_id}'")

    for section_id, expected_type in EXPECTED_SECTION_TYPES.items():
        section = sections_by_id.get(section_id)

        if not section:
            continue

        actual_type = section.get("type")

        if actual_type != expected_type:
            errors.append(
                f"section '{section_id}' must have type '{expected_type}', found '{actual_type}'"
            )

        if "enabled" not in section or not isinstance(section.get("enabled"), bool):
            errors.append(f"section '{section_id}' must have boolean 'enabled'")

        if "title" not in section or not isinstance(section.get("title"), str):
            errors.append(f"section '{section_id}' must have string 'title'")

    hero = sections_by_id.get("hero_main")
    if isinstance(hero, dict) and "contests" in hero:
        errors.append("section 'hero_main' is a banner and must not contain contests")


def validate_contest(contest, path, today, args, errors, warnings, invalid_ids, link_cache):
    before_error_count = len(errors)

    def err(message):
        errors.append(f"{path}: {message}")

    def warn(message):
        warnings.append(f"{path}: {message}")

    if not isinstance(contest, dict):
        err("contest must be an object")
        return False

    cid = str(contest.get("id", "")).strip()

    for field in REQUIRED_CONTEST_FIELDS:
        if field not in contest:
            err(f"missing '{field}'")

    if not cid:
        err("empty id")

    for field in [
        "title",
        "prize",
        "platform",
        "category",
        "deadline",
        "url",
        "imageUrl",
        "providerLogoUrl",
        "termsUrl",
    ]:
        value = contest.get(field)

        if not isinstance(value, str) or not value.strip():
            err(f"'{field}' must be a non-empty string")

    category = str(contest.get("category", "")).strip()

    if category and category not in KNOWN_CATEGORIES:
        message = f"category '{category}' is not in known categories"

        if args.strict_categories:
            err(message)
        else:
            warn(message)

    deadline = parse_deadline(contest.get("deadline"))

    if deadline is None:
        err(f"deadline '{contest.get('deadline')}' must be YYYY-MM-DD")
    elif deadline < today:
        err(f"expired contest, deadline {contest.get('deadline')}")

    if contest.get("approved") is not True:
        err("approved must be true")

    for field in REQUIRED_URL_FIELDS:
        value = contest.get(field)

        if not is_http_url(value):
            err(f"{field} must be a valid http/https URL")

    for field in OPTIONAL_IMAGE_FIELDS:
        value = contest.get(field)

        if value in (None, ""):
            continue

        if not is_http_url(value):
            err(f"{field} must be empty or a valid http/https URL")

    if args.check_links and is_http_url(contest.get("url")):
        url = contest["url"].strip()

        if url not in link_cache:
            link_cache[url] = http_status(url)

        status, ctype = link_cache[url]

        if status is None or (status >= 400 and status != 403):
            err(f"url not reachable, status={status} -> {url}")

        elif status == 403:
            warn(f"url returned 403, may block bots -> {url}")

        if is_pdf(url, ctype):
            err(f"url points to a PDF, move it to termsUrl -> {url}")

    if args.check_links and is_http_url(contest.get("termsUrl")):
        terms_url = contest["termsUrl"].strip()

        if terms_url not in link_cache:
            link_cache[terms_url] = http_status(terms_url)

        status, _ = link_cache[terms_url]

        if status is None or (status >= 400 and status != 403):
            warn(f"termsUrl not reachable, status={status} -> {terms_url}")

        elif status == 403:
            warn(f"termsUrl returned 403, may block bots -> {terms_url}")

    is_valid = len(errors) == before_error_count

    if not is_valid and cid:
        invalid_ids.add(cid)

    return is_valid


def validate_all_section(all_contests, today, args, errors, warnings, invalid_ids, link_cache):
    all_ids = set()
    all_by_id = {}

    for index, contest in enumerate(all_contests):
        path = contest_path("all", index)

        validate_contest(
            contest,
            path,
            today,
            args,
            errors,
            warnings,
            invalid_ids,
            link_cache,
        )

        if not isinstance(contest, dict):
            continue

        cid = str(contest.get("id", "")).strip()

        if not cid:
            continue

        if cid in all_ids:
            errors.append(f"{path}: duplicate id '{cid}' in section 'all'")
            invalid_ids.add(cid)
        else:
            all_ids.add(cid)
            all_by_id[cid] = contest

    return all_ids, all_by_id


def validate_secondary_sections(
    sections_by_id,
    all_ids,
    all_by_id,
    today,
    args,
    errors,
    warnings,
    invalid_ids,
    link_cache,
):
    for section_id in ["featured", "new", "expiring_soon"]:
        section = sections_by_id.get(section_id)

        if not isinstance(section, dict):
            continue

        contests = section.get("contests")

        if not isinstance(contests, list):
            errors.append(f"section '{section_id}' must contain contests array")
            continue

        section_ids = set()

        for index, contest in enumerate(contests):
            path = contest_path(section_id, index)

            validate_contest(
                contest,
                path,
                today,
                args,
                errors,
                warnings,
                invalid_ids,
                link_cache,
            )

            if not isinstance(contest, dict):
                continue

            cid = str(contest.get("id", "")).strip()

            if not cid:
                continue

            if cid in section_ids:
                errors.append(f"{path}: duplicate id '{cid}' in section '{section_id}'")
                invalid_ids.add(cid)

            section_ids.add(cid)

            if cid not in all_ids:
                errors.append(f"{path}: id '{cid}' is not present in section 'all'")
                invalid_ids.add(cid)

            if cid in all_by_id:
                master = all_by_id[cid]

                for field in REQUIRED_CONTEST_FIELDS:
                    if contest.get(field) != master.get(field):
                        warnings.append(
                            f"{path}: field '{field}' differs from section 'all'"
                        )


def validate_expiring_soon_rule(sections_by_id, all_by_id, today, errors, invalid_ids):
    section = sections_by_id.get("expiring_soon")

    if not isinstance(section, dict):
        return

    contests = section.get("contests")

    if not isinstance(contests, list):
        return

    expiring_ids = {
        contest.get("id")
        for contest in contests
        if isinstance(contest, dict) and contest.get("id")
    }

    max_date = today + timedelta(days=5)

    for cid, contest in all_by_id.items():
        deadline = parse_deadline(contest.get("deadline"))

        if deadline is None:
            continue

        should_be_expiring = today <= deadline <= max_date

        if should_be_expiring and cid not in expiring_ids:
            errors.append(
                f"expiring_soon: contest '{cid}' expires within 5 days "
                f"({contest.get('deadline')}) but is missing from expiring_soon"
            )

        if not should_be_expiring and cid in expiring_ids:
            errors.append(
                f"expiring_soon: contest '{cid}' has deadline {contest.get('deadline')} "
                f"and should not be in expiring_soon"
            )
            invalid_ids.add(cid)


def clean_invalid_contests(payload, invalid_ids):
    cleaned = copy.deepcopy(payload)

    if isinstance(cleaned, list):
        return [
            contest for contest in cleaned
            if isinstance(contest, dict) and contest.get("id") not in invalid_ids
        ]

    for page in cleaned.get("pages", []):
        if not isinstance(page, dict):
            continue

        for section in page.get("sections", []):
            if not isinstance(section, dict):
                continue

            contests = section.get("contests")

            if isinstance(contests, list):
                section["contests"] = [
                    contest for contest in contests
                    if isinstance(contest, dict)
                    and contest.get("id") not in invalid_ids
                ]

    return cleaned


def print_report(errors, warnings):
    if warnings:
        print("VALIDATION WARNINGS:\n- " + "\n- ".join(warnings), file=sys.stderr)

    if errors:
        print("VALIDATION FAILED:\n- " + "\n- ".join(errors), file=sys.stderr)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--in", dest="inp", required=True)
    parser.add_argument("--out", dest="out", default="")
    parser.add_argument("--drop-invalid", action="store_true")
    parser.add_argument("--check-links", action="store_true")
    parser.add_argument("--strict-categories", action="store_true")
    parser.add_argument("--skip-section-rules", action="store_true")
    parser.add_argument("--timezone", default="Europe/Rome")
    parser.add_argument("--today", default="", help="Override today, format YYYY-MM-DD")
    args = parser.parse_args()

    with open(args.inp, "r", encoding="utf-8") as file:
        raw_data = json.load(file)

    errors = []
    warnings = []
    invalid_ids = set()
    link_cache = {}

    today = get_today(args.timezone, args.today or None)

    try:
        mode, all_contests, payload, sections_by_id = extract_legacy_or_scova_data(raw_data)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if mode == "scova":
        validate_scova_structure(payload, sections_by_id, errors)

    all_ids, all_by_id = validate_all_section(
        all_contests,
        today,
        args,
        errors,
        warnings,
        invalid_ids,
        link_cache,
    )

    if mode == "scova":
        validate_secondary_sections(
            sections_by_id,
            all_ids,
            all_by_id,
            today,
            args,
            errors,
            warnings,
            invalid_ids,
            link_cache,
        )

        if not args.skip_section_rules:
            validate_expiring_soon_rule(
                sections_by_id,
                all_by_id,
                today,
                errors,
                invalid_ids,
            )

    print_report(errors, warnings)

    if args.out:
        output_data = raw_data

        if args.drop_invalid and invalid_ids:
            output_data = clean_invalid_contests(raw_data, invalid_ids)

        with open(args.out, "w", encoding="utf-8") as file:
            json.dump(output_data, file, ensure_ascii=False, indent=2)

        print(f"Wrote validated JSON to {args.out}")

    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
