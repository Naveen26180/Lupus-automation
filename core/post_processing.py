import re
import logging
from datetime import date, datetime
from dateutil import parser as dateutil_parser

logger = logging.getLogger(__name__)

def _is_bare_year(date_str: str) -> bool:
    """True if the standard string is just a 4-digit year (e.g. '2024')."""
    return bool(re.fullmatch(r"\b(19|20)\d{2}\b", date_str.strip()))

def _extract_year(date_str: str) -> int | None:
    """Extracts the first 4-digit year found in the string."""
    m = re.search(r"\b(19|20)\d{2}\b", date_str)
    return int(m.group()) if m else None

def _union_intervals(intervals: list[tuple[date, date]]) -> list[tuple[date, date]]:
    """Merges overlapping (start, end) date intervals."""
    if not intervals:
        return []
    intervals.sort(key=lambda x: x[0])
    merged = [intervals[0]]
    for current in intervals[1:]:
        prev_start, prev_end = merged[-1]
        curr_start, curr_end = current
        if curr_start <= prev_end:  # overlap!
            merged[-1] = (prev_start, max(prev_end, curr_end))
        else:
            merged.append(current)
    return merged

def _months_inclusive(start: date, end: date) -> int:
    """Inclusive calendar-month counting: May 2025 - March 2026 = 11 months."""
    return (end.year - start.year) * 12 + (end.month - start.month) + 1

_INTERNSHIP_KEYWORDS = re.compile(r"intern|trainee|apprentice", re.IGNORECASE)

_VOLUNTEER_KEYWORDS = re.compile(r"volunteer|unpaid|club|council", re.IGNORECASE)

def _normalize_bucket(role_title: str | None, ai_bucket: str) -> str:
    """Deterministically bucket the role now that the AI prompt no longer produces a bucket."""
    if role_title:
        if _INTERNSHIP_KEYWORDS.search(role_title):
            return "INTERNSHIP"
        if _VOLUNTEER_KEYWORDS.search(role_title):
            return "VOLUNTEER_EXTRACURRICULAR"
    return ai_bucket if ai_bucket else "FULL_TIME"

def recompute_derived_fields(parsed_response: dict, today: date) -> dict:
    role_analysis = parsed_response.get("role_analysis", [])
    final_answer = parsed_response.get("final_answer", {}).copy()
    candidate_name = final_answer.get("full_name", "Unknown Candidate")

    valid_roles = []
    
    # 1. Filter out non-role entries
    for role in role_analysis:
        role["bucket"] = _normalize_bucket(role.get("role_title"), role.get("bucket", ""))
        bucket = role["bucket"]
        employer = role.get("employer")
        
        date_raw = role.get("date_raw")
        if date_raw and not role.get("start_date_raw"):
            parts = re.split(r'\s*(?:-|to|until|till|–|—)\s*', date_raw, maxsplit=1, flags=re.IGNORECASE)
            if len(parts) == 2:
                role["start_date_raw"] = parts[0].strip()
                role["end_date_raw"] = parts[1].strip()
            else:
                role["start_date_raw"] = date_raw.strip()
                role["end_date_raw"] = None

        start_raw = role.get("start_date_raw")
        end_raw = role.get("end_date_raw")
        
        if bucket == "EDUCATION":
            continue
            
        # Part 2 backstop filter: cross-check employer against colleges to filter out leaked degree entries
        college_str = final_answer.get("college")
        if employer and college_str and isinstance(college_str, str):
            emp_lower = employer.strip().lower()
            coll_lower = college_str.lower()
            if len(emp_lower) >= 3 and emp_lower in coll_lower:
                continue

        if not employer and not start_raw and not end_raw:
            continue
        if bucket in {"INTERNSHIP", "VOLUNTEER_EXTRACURRICULAR", "FULL_TIME"} and employer:
            valid_roles.append(role)

    # 2 & 3. Parse Dates and Calculate Durations
    ongoing_keywords = {"present", "current", "till date", "ongoing"}
    default_dt = datetime(2000, 1, 1)  # Strict default so day-of-month always evaluates to 1
    
    for role in valid_roles:
        start_raw = (role.get("start_date_raw") or "").strip() or None
        end_raw = (role.get("end_date_raw") or "").strip() or None
        
        is_ongoing = False
        if end_raw and end_raw.lower() in ongoing_keywords:
            is_ongoing = True
        elif end_raw is None and start_raw is not None:
            is_ongoing = True

        role["_is_ongoing"] = is_ongoing
        role["_start_date"] = None
        role["_end_date"] = None
        role["_duration_months"] = None
        
        if (start_raw and _is_bare_year(start_raw)) or (end_raw and not is_ongoing and _is_bare_year(end_raw)):
            continue  # Bare years fail month-granularity. Leave _duration_months as None.

        # Pair years if month-only
        start_year = _extract_year(start_raw) if start_raw else None
        end_year = _extract_year(end_raw) if end_raw else None
        
        if start_raw and not start_year and end_year:
            start_raw = f"{start_raw} {end_year}"
        if end_raw and not end_year and start_year and not is_ongoing:
            end_raw = f"{end_raw} {start_year}"
            
        try:
            if start_raw:
                role["_start_date"] = dateutil_parser.parse(start_raw, fuzzy=True, default=default_dt).date()
            if is_ongoing:
                role["_end_date"] = today
            elif end_raw:
                role["_end_date"] = dateutil_parser.parse(end_raw, fuzzy=True, default=default_dt).date()

            # Reverse overlap if inferred year caused an inversion
            if role["_start_date"] and role["_end_date"] and role["_start_date"] > role["_end_date"]:
                role["_start_date"] = role["_start_date"].replace(year=role["_start_date"].year - 1)
                
            if role["_start_date"] and role["_end_date"]:
                role["_duration_months"] = max(0, _months_inclusive(role["_start_date"], role["_end_date"]))
                
        except Exception as exc:
            logger.warning("Failed to parse date for %s | Start: %s, End: %s | Error: %s", 
                           candidate_name, role.get('start_date_raw'), role.get('end_date_raw'), str(exc))

    # 4. Current Company Determination
    # current_title is taken from the SAME role as current_company, so the two
    # columns can never mismatch (e.g. two ongoing roles can't split between
    # company and title).
    full_time_roles = [r for r in valid_roles if r.get("bucket") == "FULL_TIME"]
    # Only keep ongoing ones that do NOT explicitly end before today
    ongoing_ft_roles = [r for r in full_time_roles if r["_is_ongoing"]] 

    if len(ongoing_ft_roles) == 1:
        current_role = ongoing_ft_roles[0]
    elif len(ongoing_ft_roles) > 1:
        # Pick the one with the latest parsed start_date
        ongoing_ft_roles.sort(key=lambda r: r["_start_date"] or date.min, reverse=True)
        current_role = ongoing_ft_roles[0]
    else:
        current_role = None

    current_company = current_role.get("employer") if current_role else None
    current_title = current_role.get("role_title") if current_role else None

    final_answer["current_company"] = current_company
    final_answer["current_title"] = current_title

    # 5. YOE Deterministic Calculation — always decimal years, no months split.
    # years_of_experience is always a decimal (e.g. 8 months → 0.7).
    # experience_months field is removed — it no longer exists.
    if not full_time_roles:
        final_answer["years_of_experience"] = 0
    elif all(r["_duration_months"] is None for r in full_time_roles):
        final_answer["years_of_experience"] = None
    else:
        ft_intervals = [(r["_start_date"], r["_end_date"]) for r in full_time_roles if r["_duration_months"] is not None]
        merged_ft_intervals = _union_intervals(ft_intervals)
        total_months = sum(_months_inclusive(s, e) for s, e in merged_ft_intervals)
        final_answer["years_of_experience"] = round(total_months / 12.0, 1)

    # 6. Past Companies Re-computation
    past_companies = []
    for r in valid_roles:
        b = r.get("bucket")
        emp = r.get("employer")
        if b == "FULL_TIME" and emp and emp != current_company:
            if emp not in past_companies:
                past_companies.append(emp)
                
    final_answer["past_companies"] = past_companies

    return final_answer
