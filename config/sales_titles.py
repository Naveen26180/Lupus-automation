"""Sales role title keywords used to filter job openings.

Keep this list as a plain, editable constant — not buried inside business
logic — so we can tune it without touching scraper code.

Open judgment calls (confirm with lead before finalizing):
  - "account_manager": included here but flagged — some teams consider
    this post-sales / CS, not a sales role.
  - "customer_success": NOT included by default — CS is typically post-sales.
    Add it if the lead decides it's in scope.
"""

SALES_TITLE_KEYWORDS = [
    # --- SDR / BDR ---
    "sdr",
    "bdr",
    "sales development",
    "business development representative",
    "business development rep",

    # --- Account Executives ---
    "account executive",
    " ae ",  # surrounded by spaces to avoid false matches like "ae" in "aerospace"

    # --- Enterprise / Field Sales ---
    "enterprise sales",
    "field sales",
    "regional sales",
    "territory sales",
    "territory manager",

    # --- Management ---
    "sales manager",
    "sales director",
    "vp of sales",
    "vp sales",
    "head of sales",
    "director of sales",

    # --- Individual contributor / inside/outside ---
    "sales representative",
    "sales rep",
    "inside sales",
    "outside sales",

    # --- Account Management (flagged — confirm with lead) ---
    "account manager",

    # --- General "sales" catch-all (last resort) ---
    # Intentionally broad — put it last so more specific matches win in logs.
    # "sales",  # TOO BROAD — commented out intentionally; uncomment only if needed
]
