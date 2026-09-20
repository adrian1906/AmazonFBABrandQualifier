"""
Brand Research Agent.

Gathers and organizes information about a prospective brand, manufacturer,
or distributor, from two possible sources:

  A. Manually supplied notes (always available - passed in the run input)
  B. Live web search, using the OpenAI Agents SDK's built-in WebSearchTool,
     the same hosted tool already used by the course's official
     deep_research/search_agent.py example. No scraping code is written
     here - we reuse the framework's own tool.

Web search can be switched off entirely via `allow_web_search=False`. This
is important for the fictional demo prospect in sample_prospects.py: since
that company doesn't really exist, letting the agent search the web for it
would just produce noise or hallucinated-looking results. Turning search
off forces the agent to work only from the manually supplied notes, which
keeps the fictional demo honest.

Output is a structured ResearchFindings object (see models.py) that keeps
verified facts, inferences, and unknowns clearly separated.
"""

from agents import Agent, WebSearchTool

from config import MODEL_NAME, AI_RESTRICTIONS
from models import ResearchFindings

INSTRUCTIONS = f"""
You are the Brand Research Agent for R&T Distribution Group LLC.

Your job is to gather and organize information about a prospective brand,
manufacturer, or distributor, using whatever information is provided to
you in the prompt (manually supplied notes, and - if a web search tool is
available to you - live web search results).

Organize what you find into these areas:
- Company name, website, and a short company description
- Major product categories
- Wholesale / dealer / reseller program information
- Distributor information
- Wholesale application URL, if known
- Amazon marketplace policy, if known
- MAP (Minimum Advertised Price) policy, if known
- Opening order requirement, if known
- Minimum order quantity (MOQ), if known
- Contact information, if known
- Any other relevant observations

CRITICAL RULE - separate facts from inferences:
- Put something in `verified_facts` only if it is directly stated by a
  source you were given (user-supplied notes, or a page found via web
  search).
- Put something in `inferences` if it is a reasonable guess or
  interpretation that is NOT directly confirmed - and phrase it so it is
  obviously speculative (e.g. "Likely requires a business license, based on
  typical wholesale programs in this category" rather than stating it as
  settled fact).
- Put a field's name in `unknown_fields` if you cannot determine it at all.
  Do not guess a specific value for a field you don't actually know.
- Always list your `sources` (URLs, or "user-supplied notes" if that's all
  you had).

{AI_RESTRICTIONS}

Never treat an inference as a verified fact. When in doubt, mark it unknown.
"""


def build_research_agent(allow_web_search: bool = True) -> Agent:
    """
    Construct the Research Agent.

    allow_web_search:
        True  - the agent is given WebSearchTool() and may search the web
                for real information about the prospect (use for real
                prospects with a real name/website).
        False - the agent has no tools at all and must rely entirely on
                whatever notes are included in its run input (use for the
                fictional demo prospect, or any time you want a fully
                offline/deterministic research pass).
    """
    tools = [WebSearchTool()] if allow_web_search else []
    return Agent(
        name="Brand Research Agent",
        instructions=INSTRUCTIONS,
        tools=tools,
        model=MODEL_NAME,
        output_type=ResearchFindings,
    )
