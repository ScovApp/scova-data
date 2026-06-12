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

# Whitelist morbida: warning di default, errore solo con --strict-categories.
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


def get_today(timezone: str, forced_today: str | None = None) -> date:
    if forced_today:
        return datetime.strptime(forced_today, "%Y-%m-%d").date()

    if ZoneInfo:
        return datetime.now(ZoneInfo(timezone)).date()

    return date.today()


def http_status(url: str, timeout: int = 12):
    headers = {"User-Agent": "scova-data-validator/2.0"}

    for method in ("HEAD", "GET"):
        req = urllib.request.Request(url, headers=headers, method=method)

        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                status = getattr(resp, "status", None) or resp.getcode()
                ctype = resp.headers.get("Content-Type")
                return status, ctype

        except urllib.error.HTTPError as e:
            # Alcuni server bloccano HEAD ma accettano GET.
            if method == "HEAD" and e.code in (405, 403):
                continue

            ctype = e.headers.get("Content-Type") if hasattr(e, "headers") else None
            return e.code, ctype

        except Exception:
            return None, None

    return None, None


def is_pdf(url: str, content_type):
    clean_url = url.lower().split("?")[0]

    if clean_url.endswith(".pdf"):
        return True

    if content_type and "application/pdf" in content_type.lower():
        return True

    return False


def is_http_url(value) -> bool:
    return isinstance(value, str) and bool(HTTP_RE.match(value.strip()))


def parse_deadline(value):
    if not isinstance(value, str) or not DATE_RE.match(value):
        return None

    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except Exception:
        return None


def contest_path(section_id: str, index: int) -> str:
    return f"{section_id}.contests[{index}]"


def validate_contest(
    contest,
    path,
    today,
    args,
    errors,
    warnings,
    invalid_ids,
    link_cache,
):
    before_errors = len(errors)

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

        # I campi immagine opzionali possono mancare o essere stringa vuota.
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

        # termsUrl è best-effort: warning, non errore.
        if status is None or (status >= 400 and status != 403):
            warn(f"termsUrl not reachable, status={status} -> {terms_url}")

        elif status == 403:
            warn(f"termsUrl returned 403, may block bots -> {terms_url}")

    is_valid = len(errors) == before_errors

    if not is_valid and cid:
        invalid_ids.add(cid)

    return is_valid


def get_home_page(data, errors):
    pages = data.get("pages")

    if not isinstance(pages, list):
        errors.append("root.pages must be an array")
        return None

    home_pages = [
        page for page in pages
        if isinstance(page, dict) and page.get("id") == "home"
    ]

    if not home_pages:
        errors.append("missing page with id 'home'")
        return None

    if len(home_pages) > 1:
        errors.append("duplicate page with id 'home'")

    return home_pages[0]


def get_sections_by_id(home, errors):
    sections = home.get("sections")

    if not isinstance(sections, list):
        errors.append("home.sections must be an array")
        return {}

    sections_by_id = {}

    for index, section in enumerate(sections):
        if not isinstance(section, dict):
            errors.append(f"home.sections[{index}] must be an object")
            continue

        sid = section.get("id")

        if not isinstance(sid, str) or not sid.strip():
            errors.append(f"home.sections[{index}] missing id")
            continue

        if sid in sections_by_id:
            errors.append(f"duplicate section id '{sid}'")

        sections_by_id[sid] = section

    return sections_by_id


def clean_invalid_contests(data, invalid_ids):
    cleaned = copy.deepcopy(data)

    for page in cleaned.get("pages", []):
        for section in page.get("sections", []):
            contests = section.get("contests")

            if isinstance(contests, list):
                section["contests"] = [
                    contest for contest in contests
                    if isinstance(contest, dict)
                    and contest.get("id") not in invalid_ids
                ]

    return cleaned


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
        data = json.load(file)

    errors = []
    warnings = []
    invalid_ids = set()
    link_cache = {}

    today = get_today(args.timezone, args.today or None)

    if not isinstance(data, dict):
        errors.append("contests.json root must be an object, not an array")
        print_errors(errors, warnings)
        return 2

    for field in REQUIRED_ROOT_FIELDS:
        if field not in data:
            errors.append(f"root missing '{field}'")

    if "schemaVersion" in data and not isinstance(data["schemaVersion"], int):
        errors.append("schemaVersion must be an integer")

    if "updatedAt" in data and not isinstance(data["updatedAt"], str):
        errors.append("updatedAt must be a string")

    home = get_home_page(data, errors)

    if home is None:
        print_errors(errors, warnings)
        return 2

    for field in REQUIRED_PAGE_FIELDS:
        if field not in home:
            errors.append(f"home page missing '{field}'")

    sections_by_id = get_sections_by_id(home, errors)

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

    all_section = sections_by_id.get("all")
    all_contests = all_section.get("contests") if isinstance(all_section, dict) else None

    if not isinstance(all_contests, list):
        errors.append("section 'all' must contain contests array")
        print_errors(errors, warnings)
        return 2

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
            errors.append(f"{path}: duplicate id '{cid}' in all")
            invalid_ids.add(cid)
        else:
            all_ids.add(cid)
            all_by_id[cid] = contest

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

            # Controllo morbido: se un contest è duplicato in sezione, dovrebbe
            # avere gli stessi campi principali dell'oggetto in all.
            if cid in all_by_id:
                master = all_by_id[cid]

                for field in REQUIRED_CONTEST_FIELDS:
                    if contest.get(field) != master.get(field):
                        warnings.append(
                            f"{path}: field '{field}' differs from section 'all'"
                        )

    if not args.skip_section_rules:
        validate_expiring_soon_rule(
            sections_by_id,
            all_by_id,
            today,
            errors,
            invalid_ids,
        )

    if warnings:
        print("VALIDATION WARNINGS:\n- " + "\n- ".join(warnings), file=sys.stderr)

    if errors:
        print("VALIDATION FAILED:\n- " + "\n- ".join(errors), file=sys.stderr)

    if args.out:
        output_data = data

        if args.drop_invalid and invalid_ids:
            output_data = clean_invalid_contests(data, invalid_ids)

        with open(args.out, "w", encoding="utf-8") as file:
            json.dump(output_data, file, ensure_ascii=False, indent=2)

        print(f"Wrote validated JSON to {args.out}")

    return 1 if errors else 0


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


def print_errors(errors, warnings):
    if warnings:
        print("VALIDATION WARNINGS:\n- " + "\n- ".join(warnings), file=sys.stderr)

    if errors:
        print("VALIDATION FAILED:\n- " + "\n- ".join(errors), file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
