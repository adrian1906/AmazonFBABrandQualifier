"""
Fictional demonstration data.

"Northwind Outdoor Gear Co." is a MADE-UP company invented for this demo
so the full workflow can be exercised end-to-end without contacting, or
appearing to research, any real business. It does not exist.

demo.py runs this prospect with allow_web_search=False (see workflow.py),
so the Research Agent never tries to search the web for it - it only has
the manual notes below to work from. This keeps the fictional demo from
ever being confused with real research output.
"""

from models import Prospect

# Clearly fictional - do not treat as real research.
SAMPLE_PROSPECT = Prospect(
    company_name="Northwind Outdoor Gear Co. (FICTIONAL DEMO COMPANY)",
    website="https://example-northwind-outdoor.invalid",
)

SAMPLE_MANUAL_NOTES = """
[FICTIONAL DEMO DATA - invented for demonstration purposes only]

Northwind Outdoor Gear Co. is a mid-sized outdoor equipment brand that
makes camping and hiking gear (tents, backpacks, sleeping bags). Their
website has a "Wholesale" link in the footer that leads to a short page
saying "Authorized dealer inquiries: dealers@example-northwind-outdoor.invalid"
with no further detail on MOQ, opening order, or pricing. No explicit
statement about Amazon resale or MAP policy was found on the site.
They list a handful of existing retail partners (independent outdoor
stores) but no large marketplace sellers.
""".strip()
