"""Company enrichment module.

Enriches candidate data by researching the companies they've worked for —
finding what they sell, where they sell, and who they sell to — then using
that to fill gaps left by the resume's own text.

Sub-modules (build/import order):
  company_cache      → SQLite cache layer (no network dependency)
  domain_resolver    → Resolves a company name → official domain (4-tier chain)
  company_profiler   → Scrapes a domain → structured company profile
  job_openings_scraper → Finds live sales job openings for a domain
  enrichment_pipeline → Orchestrates the above; entry point for pipeline.py
"""
