#!/usr/bin/env python3
"""Reference audit helpers: duplicate grouping, DOI verification, claim support.

Standard library only. Network use is opt-in (--online); every failure is recorded
as an explicit state rather than being turned into a negative verdict.

States are deliberately conservative:
  VERIFIED      DOI resolves and title/year/author agree
  PARTIAL       DOI resolves, one metadata field disagrees
  DOI_MISMATCH  DOI resolves to a clearly different work
  UNRESOLVED    DOI is well formed but no registration agency knows it
  INCONCLUSIVE  a lookup failed for transport/rate-limit reasons
  UNVERIFIABLE  no DOI in the record; nothing can be resolved
Nothing in this module can establish that a work is fabricated; that requires a
positive contradiction, and even then the output says "review", not "fake".
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

DOI_RE = re.compile(r"^10\.\d{4,9}/\S+$", re.I)
HAN_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
HAN_RUN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+")
LATIN_RE = re.compile(r"[a-z0-9]+")
ARXIV_DOI = re.compile(r"^10\.48550/arxiv\.(.+)$", re.I)
# Prefixes whose DOIs are registered with DataCite rather than Crossref. If DataCite
# cannot be reached, a Crossref miss says nothing about these records.
DATACITE_PREFIXES = {"10.48550", "10.5281", "10.5061", "10.17632", "10.6084", "10.1594"}
STOP = {
    "the", "and", "for", "with", "from", "into", "using", "based", "toward",
    "towards", "under", "between", "this", "that", "study", "new", "via",
}

NEGATIONS = {"not", "no", "never", "none", "cannot", "without", "fail", "fails",
             "failed", "unable", "nor", "neither", "不", "未", "无", "没有", "并非",
             "不能", "无法", "非"}


# --------------------------------------------------------------------- utils

def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def strip_tex(text: str) -> str:
    text = re.sub(r"\\(?:emph|textit|textbf|mathrm|text)\s*\{([^{}]*)\}", r"\1", text)
    text = re.sub(r"\\[A-Za-z]+\s*", " ", text)
    text = text.replace("\\_", "_").replace("{", "").replace("}", "")
    return text


def unescape_entities(text: str) -> str:
    text = re.sub(r"&#x([0-9a-fA-F]+);", lambda m: chr(int(m.group(1), 16)), text)
    text = re.sub(r"&#(\d+);", lambda m: chr(int(m.group(1))), text)
    for a, b in (("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"')):
        text = text.replace(a, b)
    return text


def fold(text: str) -> str:
    """Unicode-aware folding that keeps Han characters.

    ASCII-only folding (the common shortcut) turns every Chinese title into "",
    which silently merges unrelated entries.
    """
    text = unescape_entities(strip_tex(text or ""))
    text = unicodedata.normalize("NFKC", text).casefold()
    kept = []
    for ch in text:
        if ch.isalnum() or HAN_RE.match(ch):
            kept.append(ch)
        elif ch.isspace():
            kept.append(" ")
    return re.sub(r"\s+", "", "".join(kept))


def words(text: str) -> list[str]:
    text = unescape_entities(strip_tex(text or ""))
    text = unicodedata.normalize("NFKC", text).casefold()
    out = [w for w in LATIN_RE.findall(text) if len(w) > 2 and w not in STOP]
    for run in HAN_RUN.findall(text):
        out.extend(run[i:i + 2] for i in range(len(run) - 1))
    return list(dict.fromkeys(out))


def norm_doi(raw: str) -> str:
    d = (raw or "").strip()
    d = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", d, flags=re.I)
    d = d.replace("\\textunderscore", "_").replace("\\_", "_")
    d = re.sub(r"[.,;:\]\)}\"']+$", "", d).strip()
    return d.casefold() if DOI_RE.match(d) else ""


def main_title(title: str) -> str:
    return re.split(r"[:：—–]", strip_tex(title or ""), maxsplit=1)[0]


def ratio(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def jaccard(a: list[str], b: list[str]) -> float:
    sa, sb = set(a), set(b)
    return len(sa & sb) / len(sa | sb) if sa | sb else 0.0


# ------------------------------------------------------------------ records

def field(rec: dict, name: str) -> str:
    f = rec.get("fields") or {}
    return str(f.get(name) or rec.get(name) or "")


def authors(rec: dict) -> list[str]:
    persons = (rec.get("persons") or {}).get("author")
    if persons:
        return [str(p) for p in persons]
    raw = field(rec, "author")
    if not raw:
        return []
    return [a.strip() for a in re.split(r"\s+and\s+", raw) if a.strip()]


def surname(name: str) -> str:
    if "," in name:
        return name.split(",")[0].strip()
    parts = name.split()
    return parts[-1] if parts else name


def first_surname(rec: dict) -> str:
    a = authors(rec)
    return fold(surname(a[0])) if a else ""


def year(rec: dict) -> str:
    m = re.search(r"\d{4}", field(rec, "year") or field(rec, "date"))
    return m.group(0) if m else ""


def title(rec: dict) -> str:
    return field(rec, "title")


def load_entries(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if isinstance(data, dict):
        data = data.get("entries", [])
    if not isinstance(data, list):
        raise ValueError("bib json must be a list or an object with an 'entries' list")
    for rec in data:
        if "id" not in rec:
            raise ValueError("every record needs an id")
    return data


def save(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ------------------------------------------------------------------- dedupe

RICH_FIELDS = ("journal", "booktitle", "volume", "number", "pages", "publisher", "doi")


def richness(rec: dict) -> tuple:
    return (len([f for f in RICH_FIELDS if field(rec, f)]), field(rec, "type") in {"article", "inproceedings"})


class Union:
    def __init__(self, keys):
        self.parent = {k: k for k in keys}

    def find(self, k):
        while self.parent[k] != k:
            self.parent[k] = self.parent[self.parent[k]]
            k = self.parent[k]
        return k

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def blocking_keys(rec: dict) -> list[tuple]:
    keys = []
    sur, yr = first_surname(rec), year(rec)
    if sur:
        keys.append(("author-year", sur, yr))
    w = words(title(rec))
    if w:
        keys.append(("title-head", tuple(w[:3])))
    return keys


def dedupe(entries: list[dict]) -> dict:
    by_id = {r["id"]: r for r in entries}
    union = Union(list(by_id))
    pairs: list[dict] = []

    # 1. identical DOI: identity, not similarity
    by_doi = defaultdict(list)
    for r in entries:
        d = norm_doi(field(r, "doi"))
        if d:
            by_doi[d].append(r["id"])
    for d, ids in by_doi.items():
        for other in ids[1:]:
            union.union(ids[0], other)
            ta = fold(main_title(title(by_id[ids[0]])))
            tb = fold(main_title(title(by_id[other])))
            t_sim = ratio(ta, tb) if ta and tb else 1.0
            pairs.append({"members": [ids[0], other], "reason": "doi_exact", "doi": d,
                          "confidence": 1.0,
                          "confirmation_required": t_sim < 0.55,
                          "note": "same DOI but titles differ; likely an entry error" if t_sim < 0.55 else ""})

    # 2. preprint and published version of the same work
    for r in entries:
        d = norm_doi(field(r, "doi"))
        m = ARXIV_DOI.match(d) if d else None
        if not m:
            continue
        for other in entries:
            if other["id"] == r["id"]:
                continue
            od = norm_doi(field(other, "doi"))
            if not od or od == d:
                continue
            sim = ratio(fold(main_title(title(r))), fold(main_title(title(other))))
            if sim >= 0.95 and first_surname(r) == first_surname(other):
                union.union(other["id"], r["id"])
                pairs.append({
                    "members": [r["id"], other["id"]],
                    "reason": "preprint_published",
                    "confidence": round(sim, 3),
                    "confirmation_required": year(r) != year(other),
                    "note": "same title and first author; versions merged, published record kept as canonical",
                })

    # 3. fuzzy title match inside blocks
    blocks = defaultdict(list)
    for r in entries:
        for k in blocking_keys(r):
            blocks[k].append(r["id"])
    seen = set()
    for ids in blocks.values():
        for i, a in enumerate(ids):
            for b in ids[i + 1:]:
                if (a, b) in seen or union.find(a) == union.find(b):
                    continue
                seen.add((a, b))
                ra, rb = by_id[a], by_id[b]
                ta, tb = fold(main_title(title(ra))), fold(main_title(title(rb)))
                sim = ratio(ta, tb)
                if sim < 0.92:
                    continue
                wsim = jaccard(words(title(ra)), words(title(rb)))
                same_author = first_surname(ra) == first_surname(rb) or not first_surname(ra) or not first_surname(rb)
                da, db = norm_doi(field(ra, "doi")), norm_doi(field(rb, "doi"))
                if da and db and da != db:
                    pairs.append({"members": [a, b], "reason": "doi_conflict",
                                  "confidence": round(sim, 3), "confirmation_required": True,
                                  "note": "titles agree but DOIs differ; not merged automatically"})
                    continue
                if same_author and (sim >= 0.95 or wsim >= 0.80):
                    union.union(a, b)
                    pairs.append({"members": [a, b], "reason": "title_and_author",
                                  "confidence": round(sim, 3),
                                  "confirmation_required": sim < 0.98,
                                  "title_similarity": round(sim, 3), "word_jaccard": round(wsim, 3)})

    groups_map = defaultdict(list)
    for k in by_id:
        groups_map[union.find(k)].append(k)

    groups = []
    for root, members in groups_map.items():
        if len(members) < 2:
            continue
        canonical = max(members, key=lambda m: (richness(by_id[m]), len(json.dumps(by_id[m], ensure_ascii=False))))
        related = [p for p in pairs if set(members) & set(p["members"])]
        groups.append({
            "members": members,
            "canonical": canonical,
            "reasons": sorted({p["reason"] for p in related}),
            "confidence": min(p["confidence"] for p in related) if related else None,
            "confirmation_required": any(p["confirmation_required"] for p in related) or canonical != members[0],
            "evidence": related,
        })
    conflicts = [p for p in pairs if p["reason"] == "doi_conflict"]
    return {
        "groups": groups,
        "conflicts": conflicts,
        "merged_total": sum(len(g["members"]) - 1 for g in groups),
        "policy": "DOI equality is identity; title similarity only merges within author/year blocks; "
                  "differing DOIs are never auto-merged except preprint/published pairs.",
    }


# ------------------------------------------------------------------- verify

def http_json(url: str, timeout: int = 15, mailto: str = "") -> tuple[dict | None, str]:
    req = urllib.request.Request(url, headers={
        "User-Agent": f"refaudit/1.0 (mailto:{mailto})" if mailto else "refaudit/1.0",
        "Accept": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", "replace")), "ok"
    except urllib.error.HTTPError as exc:
        return None, "not_found" if exc.code == 404 else f"http_{exc.code}"
    except Exception as exc:  # transport, DNS, TLS, timeout
        return None, f"transport:{type(exc).__name__}"


def crossref_record(doi: str, mailto: str) -> tuple[dict | None, str]:
    url = "https://api.crossref.org/works/" + urllib.parse.quote(doi, safe="")
    if mailto:
        url += "?mailto=" + urllib.parse.quote(mailto)
    data, state = http_json(url, mailto=mailto)
    if not data:
        return None, state
    m = data.get("message", {})
    return {
        "title": " ".join(m.get("title") or []),
        "year": str((m.get("issued", {}).get("date-parts") or [[""]])[0][0] or ""),
        "authors": [f"{a.get('family', '')}, {a.get('given', '')}".strip(", ")
                    if a.get("family") else a.get("given", "")
                    for a in m.get("author", [])],
        "container": " ".join(m.get("container-title") or []),
        "publisher": m.get("publisher", ""),
        "type": m.get("type", ""),
    }, "ok"


def openalex_record(doi: str) -> tuple[dict | None, str]:
    url = "https://api.openalex.org/works/doi:" + urllib.parse.quote(doi, safe="")
    data, state = http_json(url)
    if not data:
        return None, state
    container = ""
    loc = data.get("primary_location") or {}
    if loc.get("source"):
        container = loc["source"].get("display_name", "")
    return {
        "title": data.get("title") or "",
        "year": str(data.get("publication_year") or ""),
        "authors": [((a.get("author") or {}).get("display_name") or "")
                    for a in data.get("authorships", [])],
        "container": container,
        "publisher": "",
        "type": data.get("type", ""),
    }, "ok"


def datacite_record(doi: str) -> tuple[dict | None, str]:
    url = "https://api.datacite.org/dois/" + urllib.parse.quote(doi, safe="")
    data, state = http_json(url)
    if not data:
        return None, state
    a = (data.get("data") or {}).get("attributes", {})
    return {
        "title": " ".join(t.get("title", "") for t in a.get("titles", [])),
        "year": str(a.get("publicationYear") or ""),
        "authors": [c.get("name", "") for c in a.get("creators", [])],
        "container": (a.get("container") or {}).get("title", ""),
        "publisher": a.get("publisher", ""),
        "type": "",
    }, "ok"


def handle_exists(doi: str) -> tuple[bool, str]:
    url = "https://doi.org/api/handles/" + urllib.parse.quote(doi, safe="")
    data, state = http_json(url)
    if data is None:
        return False, state
    return data.get("responseCode") == 1, "ok"


def compare(rec: dict, found: dict) -> tuple[str, list[str], dict]:
    notes: list[str] = []
    t_sim = ratio(fold(main_title(title(rec))), fold(main_title(found["title"])))
    t_sim = max(t_sim, jaccard(words(title(rec)), words(found["title"])))
    y_rec, y_found = year(rec), found.get("year", "")
    a_rec, a_found = first_surname(rec), fold(surname(found["authors"][0])) if found.get("authors") else ""
    checks = {
        "title_similarity": round(t_sim, 3),
        "year": [y_rec, y_found],
        "first_author": [a_rec, a_found],
    }
    if t_sim < 0.55:
        return "DOI_MISMATCH", ["title differs from the registered record"], checks
    if t_sim < 0.85:
        notes.append("title partially matches (subtitle or translation difference)")
    if y_rec and y_found and y_rec != y_found:
        notes.append(f"year differs: entry {y_rec}, record {y_found}")
    if a_rec and a_found and a_rec != a_found:
        notes.append("first author differs")
    if notes:
        return "PARTIAL", notes, checks
    return "VERIFIED", ["title, year and first author agree"], checks


def verify(entries: list[dict], online: bool, cache_path: Path, mailto: str) -> dict:
    cache = {}
    if cache_path.exists():
        try:
            cache = json.loads(cache_path.read_text(encoding="utf-8"))
        except ValueError:
            cache = {}
    out = []
    for rec in entries:
        doi = norm_doi(field(rec, "doi"))
        item = {"id": rec["id"], "doi": doi, "checked_at": now()}
        if not doi:
            item.update({"verdict": "UNVERIFIABLE", "reason": "no DOI in the record"})
            out.append(item)
            continue
        if doi in cache and not online:
            item.update(cache[doi])
            item["from_cache"] = True
            out.append(item)
            continue

        found, source, state = None, "", "not_attempted"
        states = {}
        if online:
            found, state = crossref_record(doi, mailto)
            states["crossref"] = state
            if found:
                source = "crossref"
            elif state == "not_found":
                found, state = openalex_record(doi)
                states["openalex"] = state
                if found:
                    source = "openalex"
            if not found and state == "not_found":
                found, state = datacite_record(doi)
                states["datacite"] = state
                if found:
                    source = "datacite"
            if not found and state == "not_found":
                ok, hstate = handle_exists(doi)
                states["handle"] = hstate
                state = "no_registration_agency" if hstate == "ok" and not ok else hstate

        if found:
            verdict, notes, checks = compare(rec, found)
            item.update({"verdict": verdict, "source": source, "registered_title": found["title"],
                         "registered_year": found["year"], "notes": notes, "checks": checks,
                         "source_states": states})
            cache[doi] = {"verdict": verdict, "source": source, "registered_title": found["title"],
                          "registered_year": found["year"], "notes": notes, "checks": checks,
                          "checked_at": item["checked_at"]}
        else:
            verdict = "UNRESOLVED" if state in {"not_found", "no_registration_agency"} else "INCONCLUSIVE"
            if (verdict == "INCONCLUSIVE" and states.get("crossref") == "not_found"
                    and doi.split("/")[0] not in DATACITE_PREFIXES):
                # Crossref is authoritative for its own prefixes; the other registries
                # could not be reached, so this is "not in Crossref", not "does not exist".
                verdict = "UNRESOLVED"
                item["scope"] = "crossref only; DataCite and the handle registry were unreachable"
            item.update({"verdict": verdict, "reason": state, "source_states": states})
        out.append(item)

    if online:
        save(cache_path, cache)
    summary = defaultdict(int)
    for i in out:
        summary[i["verdict"]] += 1
    return {"records": out, "summary": dict(summary), "online": online,
            "policy": "INCONCLUSIVE means a lookup failed; it is not evidence of fabrication. "
                      "Only a resolving DOI that points at a different work yields DOI_MISMATCH."}


# ------------------------------------------------------------------- support

def sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[。！？.!?])\s*", text or "") if s.strip()]


def support_one(claim: dict) -> dict:
    text = claim.get("text", "")
    abstract = claim.get("abstract") or ""
    title = claim.get("title") or ""
    if not abstract and not title:
        return {"claim_id": claim.get("claim_id"), "verdict": "UNVERIFIABLE",
                "reason": "no abstract or title supplied"}
    cw = words(text)
    aw = set(words(abstract)) | set(words(title))
    coverage = len([w for w in cw if w in aw]) / len(cw) if cw else 0.0
    nums = set(re.findall(r"\d+(?:\.\d+)?", text))
    nums_abs = set(re.findall(r"\d+(?:\.\d+)?", abstract))
    missing_numbers = sorted(n for n in nums if n not in nums_abs) if nums and abstract else []
    neg_claim = bool({w for w in re.findall(r"[a-z]+|[\u4e00-\u9fff]", text.lower())} & NEGATIONS)
    neg_abs = bool({w for w in re.findall(r"[a-z]+|[\u4e00-\u9fff]", abstract.lower())} & NEGATIONS)

    best, best_score = "", 0.0
    for s in sentences(abstract):
        sw = set(words(s))
        if not sw:
            continue
        score = len(sw & set(cw)) / len(sw)
        if score > best_score:
            best, best_score = s, score

    verdict = "SUPPORTED" if coverage >= 0.5 else "PARTIAL" if coverage >= 0.25 else "NOT_SUPPORTED"
    notes = []
    if missing_numbers:
        notes.append(f"claim numbers absent from abstract: {', '.join(missing_numbers)}")
        verdict = "PARTIAL" if verdict == "SUPPORTED" else verdict
    if neg_claim != neg_abs and abstract:
        notes.append("negation differs between claim and abstract")
        verdict = "PARTIAL" if verdict == "SUPPORTED" else verdict
    return {
        "claim_id": claim.get("claim_id"), "verdict": verdict,
        "coverage": round(coverage, 3), "evidence": best, "evidence_score": round(best_score, 3),
        "notes": notes, "method": "lexical overlap, not entailment",
    }


def support(claims: list[dict]) -> dict:
    rows = [support_one(c) for c in claims]
    summary = defaultdict(int)
    for r in rows:
        summary[r["verdict"]] += 1
    return {"claims": rows, "summary": dict(summary),
            "policy": "Lexical overlap is a triage signal. PARTIAL or NOT_SUPPORTED means a human or an "
                      "entailment model must read the abstract before the citation is kept."}


# ------------------------------------------------------------------- markers

MARK_RE = re.compile(r"\[(\d+(?:\s*[-–,]\s*\d+)*)\]")
BIB_HEAD_RE = re.compile(r"^\s*#{0,6}\s*(参考文献|references|bibliography)\b[^\n]*$",
                         re.I | re.M)
BIB_LINE_RE = re.compile(r"^\s*\[(\d+)\]\s+\S")


def expand_marker(text: str) -> list[int]:
    nums: list[int] = []
    for part in re.split(r"\s*,\s*", text.strip()):
        m = re.match(r"^(\d+)\s*[-–]\s*(\d+)$", part)
        if m:
            nums.extend(range(int(m.group(1)), int(m.group(2)) + 1))
        elif part.strip().isdigit():
            nums.append(int(part.strip()))
    return nums


def audit_markers(manuscript: str, numbering: dict) -> dict:
    """Compare citation markers in a rendered manuscript against the expected
    numbering, without re-deriving anything by hand.

    The body is everything before a 参考文献/References heading; markers there are
    the observed clusters in order of appearance. Numbered lines after the heading
    are the observed bibliography.
    """
    head = BIB_HEAD_RE.search(manuscript)
    body = manuscript[:head.start()] if head else manuscript
    bibtext = manuscript[head.end():] if head else ""

    observed = [expand_marker(m.group(1)) for m in MARK_RE.finditer(body)]
    expected = [list(c.get("numbers", [])) for c in numbering.get("clusters", [])]

    discrepancies: list[dict] = []
    for i, exp in enumerate(expected):
        obs = observed[i] if i < len(observed) else None
        if obs != exp:
            discrepancies.append({"kind": "body_cluster", "index": i,
                                  "expected": exp, "observed": obs})
    if len(observed) != len(expected):
        discrepancies.append({"kind": "body_count",
                              "expected": len(expected), "observed": len(observed)})

    bib_observed = None
    if bibtext:
        bib_observed = [int(m.group(1)) for ln in bibtext.splitlines()
                        if (m := BIB_LINE_RE.match(ln))]
        bib_expected = [b.get("number") for b in numbering.get("bibliography", [])]
        if bib_observed != bib_expected:
            discrepancies.append({"kind": "bibliography",
                                  "expected": bib_expected, "observed": bib_observed})

    return {
        "ok": not discrepancies,
        "markers_found": len(observed),
        "bibliography_entries": len(bib_observed) if bib_observed is not None else None,
        "discrepancies": discrepancies,
        "note": "markers are matched by position and value; the skill still checks "
                "manually whether each citation supports its sentence",
    }


# ---------------------------------------------------------------------- cli

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="mode", required=True)

    d = sub.add_parser("dedupe", help="group duplicate bibliographic records")
    d.add_argument("bib"); d.add_argument("out")

    v = sub.add_parser("verify", help="resolve DOIs and compare metadata")
    v.add_argument("bib"); v.add_argument("out")
    v.add_argument("--online", action="store_true")
    v.add_argument("--cache", default="")
    v.add_argument("--mailto", default="")

    s = sub.add_parser("support", help="check whether a cited work supports a claim")
    s.add_argument("claims"); s.add_argument("out")

    mk = sub.add_parser("audit-markers", help="check a rendered manuscript against expected numbering")
    mk.add_argument("manuscript"); mk.add_argument("numbering"); mk.add_argument("out")

    a = ap.parse_args()
    if a.mode == "dedupe":
        save(Path(a.out), dedupe(load_entries(Path(a.bib))))
    elif a.mode == "verify":
        cache = Path(a.cache) if a.cache else Path(a.out).with_name("doi_cache.json")
        save(Path(a.out), verify(load_entries(Path(a.bib)), a.online, cache, a.mailto))
    elif a.mode == "support":
        data = json.loads(Path(a.claims).read_text(encoding="utf-8-sig"))
        save(Path(a.out), support(data))
    else:
        manuscript = Path(a.manuscript).read_text(encoding="utf-8-sig")
        numbering = json.loads(Path(a.numbering).read_text(encoding="utf-8-sig"))
        save(Path(a.out), audit_markers(manuscript, numbering))
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError) as exc:
        print(json.dumps({"ok": False, "error": type(exc).__name__, "detail": str(exc)},
                         ensure_ascii=False))
        sys.exit(2)
