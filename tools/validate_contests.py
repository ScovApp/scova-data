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

REQUIRED_IMAGE_FIELDS = [
    "imageUrl",
    "providerLogoUrl",
    "cover",
    "coverUrl",
    "brandImage",
    "ogImage",
    "twitterImage",
    "image",
]

OPTIONAL_BUT_VALIDATED_URL_FIELDS = [
    "cover",
    "coverUrl",
    "brandImage",
    "ogImage",
    "twitterImage",
    "image",
]

REQUIRED_CONTEST_FIELDS = [
    "id",
    "title",
    "prize",
    "platform",
    "category",
    "mechanic",
    "badges",
    "deadline",
    "url",
    "termsUrl",
    "approved",
    *REQUIRED_IMAGE_FIELDS,
]

REQUIRED_STRING_FIELDS = [
    "id",
    "title",
    "prize",
    "platform",
    "category",
    "mechanic",
    "deadline",
    "url",
    "termsUrl",
    "imageUrl",
    "providerLogoUrl",
]

REQUIRED_URL_FIELDS = ["url", "imageUrl", "providerLogoUrl", "termsUrl"]

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

KNOWN_MECHANICS = {
    "instant_win",
    "final_draw",
    "periodic_draw",
    "purchase_required",
    "unique_code",
    "quiz",
    "play_and_win",
    "creative_contest",
    "vote_and_win",
    "referral",
    "missions",
    "rush_and_win",
}

KNOWN_BADGES = {
    "Vinci subito",
    "Estrazione",
    "Estrazione finale",
    "Estrazione periodica",
    "Con scontrino",
    "Senza acquisto",
    "Quiz",
    "Gioco",
    "Creativo",
    "Codice",
    "Missioni",
    "Voto",
    "Referral",
    "A tempo",
    "Rush",
    "Acquisto",
    "Premio certo",
    "Gratis",
    "Online",
}

MECHANIC_SUGGESTED_BADGES = {
    "instant_win": {"Vinci subito"},
    "final_draw": {"Estrazione", "Estrazione finale"},
    "periodic_draw": {"Estrazione", "Estrazione periodica"},
    "purchase_required": {"Con scontrino", "Acquisto"},
    "unique_code": {"Codice"},
    "quiz": {"Quiz"},
    "play_and_win": {"Gioco"},
    "creative_contest": {"Creativo"},
    "vote_and_win": {"Voto"},
    "referral": {"Referral"},
    "missions": {"Missioni"},
    "rush_and_win": {"Rush", "A tempo"},
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


def parse_updated_at(value):
    if not isinstance(value, str) or not value.strip():
        return None

    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None


def http_status(url, timeout=12):
    headers = {"User-Agent": "scova-data-validator/3.0"}

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


def get_sections_by_id(payload):
    pages = payload.get("pages")
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

    return home, sections_by_id


def extract_legacy_or_scova_data(raw_data):
    if isinstance(raw_data, list):
        return "legacy", raw_data, raw_data, {}

    if not isinstance(raw_data, dict):
        raise ValueError("contests.json root must be an object or an array")

    _, sections_by_id = get_sections_by_id(raw_data)

    all_section = sections_by_id.get("all")

    if not all_section:
        raise ValueError("missing section with id 'all'")

    if all_section.get("type") != "contest_grid":
        raise ValueError("section 'all' must have type 'contest_grid'")

    contests = all_section.get("contests")

    if not isinstance(contests, list):
        raise ValueError("section 'all.contests' must be an array")

    return "scova", contests, raw_data, sections_by_id


def load_previous_ids(previous_path):
    if not previous_path:
        return None

    with open(previous_path, "r", encoding="utf-8") as file:
        previous_data = json.load(file)

    _, previous_all_contests, _, _ = extract_legacy_or_scova_data(previous_data)

    return {
        str(contest.get("id", "")).strip()
        for contest in previous_all_contests
        if isinstance(contest, dict) and str(contest.get("id", "")).strip()
    }


def validate_scova_structure(payload, sections_by_id, errors):
    for field in REQUIRED_ROOT_FIELDS:
        if field not in payload:
            errors.append(f"root missing '{field}'")

    if "schemaVersion" in payload and not isinstance(payload["schemaVersion"], int):
        errors.append("schemaVersion must be an integer")

    if "updatedAt" in payload:
        if not isinstance(payload["updatedAt"], str):
            errors.append("updatedAt must be a string")
        elif parse_updated_at(payload["updatedAt"]) is None:
            errors.append("updatedAt must be a valid ISO datetime string")

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

    if home.get("category") != "HUB_HOME":
        errors.append("home.category must be 'HUB_HOME'")

    sections = home.get("sections")
    if not isinstance(sections, list):
        errors.append("home.sections must be an array")
        return

    actual_section_ids = [
        section.get("id")
        for section in sections
        if isinstance(section, dict)
    ]

    if actual_section_ids != REQUIRED_SECTION_IDS:
        errors.append(
            "home.sections order must be "
            + ", ".join(REQUIRED_SECTION_IDS)
            + f"; found {actual_section_ids}"
        )

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

        if section_id != "hero_main" and not isinstance(section.get("contests"), list):
            errors.append(f"section '{section_id}' must contain contests array")

    hero = sections_by_id.get("hero_main")
    if isinstance(hero, dict) and "contests" in hero:
        errors.append("section 'hero_main' is a banner and must not contain contests")


def validate_badges(contest, path, args, errors, warnings):
    def err(message):
        errors.append(f"{path}: {message}")

    def warn(message):
        warnings.append(f"{path}: {message}")

    badges = contest.get("badges")

    if not isinstance(badges, list):
        err("'badges' must be an array of strings")
        return

    if not badges:
        err("'badges' must contain at least 1 badge")
        return

    if len(badges) > args.max_badges:
        err(f"'badges' must contain at most {args.max_badges} badges")

    seen = set()

    for badge_index, badge in enumerate(badges):
        badge_path = f"{path}.badges[{badge_index}]"

        if not isinstance(badge, str) or not badge.strip():
            errors.append(f"{badge_path}: badge must be a non-empty string")
            continue

        clean_badge = badge.strip()

        if clean_badge != badge:
            errors.append(f"{badge_path}: badge must not have leading/trailing spaces")

        if clean_badge in seen:
            errors.append(f"{badge_path}: duplicate badge '{clean_badge}'")

        seen.add(clean_badge)

        if clean_badge not in KNOWN_BADGES:
            message = f"badge '{clean_badge}' is not in known badges"

            if args.strict_badges:
                errors.append(f"{badge_path}: {message}")
            else:
                warnings.append(f"{badge_path}: {message}")

    mechanic = str(contest.get("mechanic", "")).strip()
    suggested = MECHANIC_SUGGESTED_BADGES.get(mechanic)

    if suggested and not (set(str(b).strip() for b in badges if isinstance(b, str)) & suggested):
        warn(
            f"mechanic '{mechanic}' usually should have one of these badges: "
            + ", ".join(sorted(suggested))
        )


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

    for field in REQUIRED_STRING_FIELDS:
        value = contest.get(field)

        if not isinstance(value, str) or not value.strip():
            err(f"'{field}' must be a non-empty string")
        elif value != value.strip():
            err(f"'{field}' must not have leading/trailing spaces")

    category = str(contest.get("category", "")).strip()

    if category and category not in KNOWN_CATEGORIES:
        message = f"category '{category}' is not in known categories"

        if args.strict_categories:
            err(message)
        else:
            warn(message)

    mechanic = str(contest.get("mechanic", "")).strip()

    if mechanic and mechanic not in KNOWN_MECHANICS:
        err(
            f"mechanic '{mechanic}' is not valid. Allowed values: "
            + ", ".join(sorted(KNOWN_MECHANICS))
        )

    validate_badges(contest, path, args, errors, warnings)

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

    for field in OPTIONAL_BUT_VALIDATED_URL_FIELDS:
        value = contest.get(field)

        if value in (None, ""):
            if args.strict_images:
                err(f"{field} must be a valid http/https URL")
            else:
                warn(f"{field} is empty; field is present but fallback image is recommended")
            continue

        if not isinstance(value, str):
            err(f"{field} must be a string URL or empty string")
            continue

        if not is_http_url(value):
            err(f"{field} must be empty or a valid http/https URL")

    if args.check_links and is_http_url(contest.get("url")):
        url = contest["url"].strip()

        if url not in link_cache:
            link_cache[url] = http_status(url)

        status, ctype = link_cache[url]

        if status is None or status >= 400:
            warn(f"url not reachable, status={status} -> {url}")

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


def validate_new_section_excludes_expiring(sections_by_id, today, errors, invalid_ids):
    section = sections_by_id.get("new")

    if not isinstance(section, dict):
        return

    contests = section.get("contests")

    if not isinstance(contests, list):
        return

    max_date = today + timedelta(days=5)

    for index, contest in enumerate(contests):
        if not isinstance(contest, dict):
            continue

        cid = str(contest.get("id", "")).strip()
        deadline = parse_deadline(contest.get("deadline"))

        if not cid or deadline is None:
            continue

        if today <= deadline <= max_date:
            errors.append(
                f"new.contests[{index}]: contest '{cid}' expires within 5 days "
                f"({contest.get('deadline')}) and must be only in expiring_soon, not in new"
            )
            invalid_ids.add(cid)


def validate_new_section_with_previous(
    sections_by_id,
    all_by_id,
    previous_ids,
    today,
    errors,
    invalid_ids,
):
    if previous_ids is None:
        return

    new_section = sections_by_id.get("new")
    expiring_section = sections_by_id.get("expiring_soon")

    if not isinstance(new_section, dict):
        return

    new_contests = new_section.get("contests")
    expiring_contests = expiring_section.get("contests") if isinstance(expiring_section, dict) else []

    if not isinstance(new_contests, list):
        return

    if not isinstance(expiring_contests, list):
        expiring_contests = []

    new_ids = {
        str(contest.get("id", "")).strip()
        for contest in new_contests
        if isinstance(contest, dict) and str(contest.get("id", "")).strip()
    }

    expiring_ids = {
        str(contest.get("id", "")).strip()
        for contest in expiring_contests
        if isinstance(contest, dict) and str(contest.get("id", "")).strip()
    }

    max_date = today + timedelta(days=5)

    for cid in sorted(new_ids):
        if cid in previous_ids:
            errors.append(
                f"new: contest '{cid}' already existed in previous file and should not be in new"
            )
            invalid_ids.add(cid)

    for cid, contest in all_by_id.items():
        if cid in previous_ids:
            continue

        deadline = parse_deadline(contest.get("deadline"))
        is_expiring = deadline is not None and today <= deadline <= max_date

        if is_expiring:
            if cid not in expiring_ids:
                errors.append(
                    f"new/expiring rule: newly added contest '{cid}' expires within 5 days "
                    f"({contest.get('deadline')}) and must be in expiring_soon"
                )
            if cid in new_ids:
                errors.append(
                    f"new/expiring rule: newly added contest '{cid}' expires within 5 days "
                    "and must not be in new"
                )
                invalid_ids.add(cid)
        else:
            if cid not in new_ids:
                errors.append(
                    f"new: contest '{cid}' is not in previous file and should be in new"
                )


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
    parser.add_argument("--previous", default="", help="Optional previous canonical JSON for stronger 'new' validation")
    parser.add_argument("--drop-invalid", action="store_true")
    parser.add_argument("--check-links", action="store_true")
    parser.add_argument("--strict-categories", action="store_true")
    parser.add_argument("--strict-badges", action="store_true")
    parser.add_argument("--strict-images", action="store_true")
    parser.add_argument("--skip-section-rules", action="store_true")
    parser.add_argument("--skip-new-rules", action="store_true")
    parser.add_argument("--max-badges", type=int, default=3)
    parser.add_argument("--timezone", default="Europe/Rome")
    parser.add_argument("--today", default="", help="Override today, format YYYY-MM-DD")
    args = parser.parse_args()

    if args.max_badges < 1:
        print("ERROR: --max-badges must be >= 1", file=sys.stderr)
        return 2

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

    previous_ids = None
    if args.previous:
        try:
            previous_ids = load_previous_ids(args.previous)
        except Exception as exc:
            print(f"ERROR: unable to load --previous file: {exc}", file=sys.stderr)
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

        if not args.skip_new_rules:
            validate_new_section_excludes_expiring(
                sections_by_id,
                today,
                errors,
                invalid_ids,
            )

            validate_new_section_with_previous(
                sections_by_id,
                all_by_id,
                previous_ids,
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
