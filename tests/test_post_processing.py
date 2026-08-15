from datetime import date
from core.post_processing import recompute_derived_fields

def build_payload(roles_list):
    return {
        "role_analysis": roles_list,
        "final_answer": {"full_name": "Test User"} # Mocks minimum final_answer
    }

def test_role_ending_1_day_before_today():
    today = date(2026, 7, 15)
    payload = build_payload([{
        "employer": "Past Corp", 
        "bucket": "FULL_TIME",
        "start_date_raw": "Jan 2026", 
        "end_date_raw": "Jul 14 2026"
    }])
    result = recompute_derived_fields(payload, today)
    assert result["current_company"] is None # Case 1

def test_role_present_is_ongoing():
    today = date(2026, 7, 15)
    payload = build_payload([{
        "employer": "Present Corp", 
        "bucket": "FULL_TIME",
        "start_date_raw": "Jan 2026", 
        "end_date_raw": "Present"
    }])
    result = recompute_derived_fields(payload, today)
    assert result["current_company"] == "Present Corp" # Case 2

def test_overlapping_full_time_roles():
    today = date(2026, 7, 15)
    payload = build_payload([
        {"employer": "Company A", "bucket": "FULL_TIME", "start_date_raw": "Jan 2024", "end_date_raw": "Dec 2024"}, # 12 mos
        {"employer": "Company B", "bucket": "FULL_TIME", "start_date_raw": "Nov 2024", "end_date_raw": "Feb 2025"}  # overlaps N/D. Total is Jan24-Feb25 = 14 mos
    ])
    result = recompute_derived_fields(payload, today)
    assert result["years_of_experience"] == round(14/12.0, 1) # Case 4

def test_bare_year_excluded():
    today = date(2026, 7, 15)
    payload = build_payload([{
        "employer": "Bare Year Corp", 
        "bucket": "FULL_TIME",
        "start_date_raw": "2023", 
        "end_date_raw": "2024"
    }])
    result = recompute_derived_fields(payload, today)
    # Does not crash, excluded from math, YOE is None (because this is the ONLY fulltime role)
    assert result["years_of_experience"] is None # Case 5 & 7

def test_zero_full_time_roles():
    """Internship-only resume: no full-time roles → years_of_experience == 0.
    Internship time must NOT bleed into the YOE sum.
    internship_experience field must NOT exist in the result (it was removed).
    """
    today = date(2026, 7, 15)
    payload = build_payload([{
        "employer": "Intern Corp", 
        "bucket": "INTERNSHIP",
        "start_date_raw": "Jan 2025", 
        "end_date_raw": "March 2025"
    }])
    result = recompute_derived_fields(payload, today)
    assert result["years_of_experience"] == 0  # No full-time — genuinely zero
    assert result.get("experience_months") is None  # Not populated either
    assert "internship_experience" not in result  # Field must be gone entirely

def test_internship_only_resume_no_crash():
    """Edge case: resume has multiple internship roles, no full-time at all.
    Pipeline must complete without crashing.
    YOE must be 0 (not null — null means 'data existed but was unparseable').
    Internship time must NOT be combined into years_of_experience.
    """
    today = date(2026, 7, 15)
    payload = build_payload([
        {"employer": "Intern Corp A", "bucket": "INTERNSHIP", "start_date_raw": "Jan 2025", "end_date_raw": "March 2025"},
        {"employer": "Intern Corp B", "bucket": "INTERNSHIP", "start_date_raw": "Jun 2025", "end_date_raw": "Dec 2025"},
    ])
    result = recompute_derived_fields(payload, today)
    assert result["years_of_experience"] == 0   # Zero full-time → output exactly 0
    assert result.get("experience_months") is None
    assert result.get("current_company") is None
    assert "internship_experience" not in result  # Field must be gone entirely

def test_internship_role_excluded_from_yoe():
    """Candidate has both internship and full-time. Internship months must NOT
    be included in the years_of_experience or experience_months calculation.
    """
    today = date(2026, 7, 15)
    payload = build_payload([
        {"employer": "Intern Corp", "bucket": "INTERNSHIP", "start_date_raw": "Jan 2024", "end_date_raw": "Jun 2024"},  # 6 months internship
        {"employer": "Real Corp",   "bucket": "FULL_TIME",   "start_date_raw": "Aug 2024", "end_date_raw": "Jul 2025"},  # 12 months FT
    ])
    result = recompute_derived_fields(payload, today)
    # Only the 12-month FULL_TIME role should count
    assert result["years_of_experience"] == 1.0
    assert result.get("experience_months") is None
    assert "internship_experience" not in result

def test_month_only_inference():
    today = date(2026, 7, 15)
    payload = build_payload([{
        "employer": "Missing Year Corp", 
        "bucket": "FULL_TIME",
        "start_date_raw": "Aug", 
        "end_date_raw": "Oct 2023"
    }])
    result = recompute_derived_fields(payload, today)
    # Aug-Oct 2023 = 3 months = 0.3 years (rounded to 1dp)
    assert result["years_of_experience"] == round(3 / 12.0, 1)
    assert "experience_months" not in result

def test_education_entries_excluded_from_past_companies():
    today = date(2026, 7, 15)
    payload = {
        "role_analysis": [
            {
                "role_title": "Software Engineer",
                "employer": "Present Corp",
                "bucket": "FULL_TIME",
                "start_date_raw": "Jan 2026",
                "end_date_raw": "Present"
            },
            {
                "role_title": "Master of Business Administration",
                "employer": "SRM University",
                "bucket": "VOLUNTEER_EXTRACURRICULAR",
                "start_date_raw": "Jan 2025",
                "end_date_raw": "Present"
            },
            {
                "role_title": "Bachelor of Commerce",
                "employer": "Vellore Institute of Technology",
                "bucket": "VOLUNTEER_EXTRACURRICULAR",
                "start_date_raw": "Nov 2022",
                "end_date_raw": "Aug 2025"
            }
        ],
        "final_answer": {
            "full_name": "Test User",
            "college": "SRM University, Chennai, Tamil Nadu, Vellore Institute of Technology, Vellore, Tamil Nadu"
        }
    }
    result = recompute_derived_fields(payload, today)
    # Check that past_companies is empty (since SRM University and Vellore Institute of Technology are both excluded by matching the college field)
    assert result["past_companies"] == []

def test_hybrid_yoe_eleven_months():
    """11 months of FULL_TIME experience should yield years_of_experience = 0.9
    (i.e. round(11/12, 1)). No experience_months field should be present.
    """
    today = date(2026, 7, 15)
    payload = build_payload([{
        "employer": "Startup Singam",
        "bucket": "FULL_TIME",
        "start_date_raw": "May 2025",
        "end_date_raw": "March 2026"
    }])
    result = recompute_derived_fields(payload, today)
    assert result["years_of_experience"] == round(11 / 12.0, 1)
    assert "experience_months" not in result

def test_hybrid_yoe_thirty_months():
    today = date(2026, 7, 15)
    payload = build_payload([{
        "employer": "Startup Singam",
        "bucket": "FULL_TIME",
        "start_date_raw": "Jan 2024",
        "end_date_raw": "June 2026"
    }])
    result = recompute_derived_fields(payload, today)
    assert result["years_of_experience"] == 2.5
    assert "experience_months" not in result


def test_short_tenure_always_decimal_yoe():
    """8 months of FULL_TIME experience must yield years_of_experience = 0.7
    (not a separate experience_months field — that field no longer exists).
    """
    today = date(2026, 7, 15)
    payload = build_payload([{
        "employer": "Short Stint Ltd",
        "bucket": "FULL_TIME",
        "start_date_raw": "Aug 2025",
        "end_date_raw": "March 2026"  # Aug-Mar inclusive = 8 months
    }])
    result = recompute_derived_fields(payload, today)
    assert result["years_of_experience"] == round(8 / 12.0, 1)
    assert "experience_months" not in result
    assert result.get("experience_months") is None  # must not be populated
