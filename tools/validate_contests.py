
#!/usr/bin/env python3
import argparse, json, re, sys, urllib.request, urllib.error
from datetime import date, datetime

ALLOWED_CATEGORIES = {
  "Tech","Shopping","Viaggi","Musica","Senza Acquisto","Gare","Giochi","Benessere",
  "Sport","Tempo Libero","Gastronomia","Moda"
}
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

def http_status(url: str, timeout: int = 12):
    headers = {"User-Agent": "scova-data-validator/1.0"}
    for method in ("HEAD","GET"):
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
            return None, None
    return None, None

def is_pdf(url: str, content_type):
    if url.lower().split("?")[0].endswith(".pdf"):
        return True
    if content_type and "application/pdf" in content_type.lower():
        return True
    return False

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", required=True)
    ap.add_argument("--out", dest="out", default="")
    ap.add_argument("--drop-invalid", action="store_true")
    ap.add_argument("--check-links", action="store_true")
    args = ap.parse_args()

    with open(args.inp, "r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, list):
        print("ERROR: contests.json must be a JSON array", file=sys.stderr)
        return 2

    seen_ids = set()
    today = date.today()
    errors = []
    cleaned = []

    def err(msg): errors.append(msg)

    for i, c in enumerate(data):
        prefix = f"[{i}]"
        if not isinstance(c, dict):
            err(f"{prefix} not an object")
            continue

        for k in ("id","title","prize","platform","category","deadline","url","approved"):
            if k not in c:
                err(f"{prefix} missing '{k}'")

        cid = str(c.get("id","")).strip()
        if not cid:
            err(f"{prefix} empty id")
        elif cid in seen_ids:
            err(f"{prefix} duplicate id '{cid}'")
        else:
            seen_ids.add(cid)

        cat = str(c.get("category","")).strip()
        if cat and cat not in ALLOWED_CATEGORIES:
            err(f"{prefix} category '{cat}' not allowed")

        dl = c.get("deadline")
        dl_ok = False
        if isinstance(dl, str) and DATE_RE.match(dl):
            try:
                dl_date = datetime.strptime(dl, "%Y-%m-%d").date()
                dl_ok = True
                if dl_date < today:
                    err(f"{prefix} expired (deadline {dl})")
            except Exception:
                err(f"{prefix} invalid deadline '{dl}'")
        else:
            err(f"{prefix} deadline '{dl}' must be YYYY-MM-DD")

        if c.get("approved") is not True:
            err(f"{prefix} approved must be true")

        url = c.get("url","")
        if not isinstance(url, str) or not url.strip().startswith("http"):
            err(f"{prefix} url invalid")
        elif args.check_links:
            status, ctype = http_status(url.strip())
            if status is None or status >= 400:
                err(f"{prefix} url not reachable (status={status}) -> {url}")
            if is_pdf(url.strip(), ctype):
                err(f"{prefix} url points to a PDF (move it to termsUrl) -> {url}")

        terms = c.get("termsUrl")
        if terms is not None:
            if not isinstance(terms, str) or not terms.strip().startswith("http"):
                err(f"{prefix} termsUrl invalid")
            elif args.check_links:
                status, _ = http_status(terms.strip())
                if status is None or status >= 400:
                    err(f"{prefix} termsUrl not reachable (status={status}) -> {terms}")

        is_valid = not any(e.startswith(prefix) for e in errors)

        if is_valid or not args.drop_invalid:
            cleaned.append(c)

    if errors:
        print("VALIDATION FAILED:\n- " + "\n- ".join(errors), file=sys.stderr)

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(cleaned, f, ensure_ascii=False, indent=2)
        print(f"Wrote {len(cleaned)} items to {args.out}")

    return 1 if errors else 0

if __name__ == "__main__":
    raise SystemExit(main())

