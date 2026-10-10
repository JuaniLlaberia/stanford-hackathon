"""Who relies on each answer and what they would do with it. Policy is the same; stakes/wording change."""
from agent.schema import Persona

PERSONAS: dict[str, Persona] = {
    "analyst": Persona(key="analyst", who="Equity research analyst", action="put the figures in a client note",
                       sign_off="Research lead"),
    "comms": Persona(key="comms", who="Comms lead", action="publish the piece on the company blog",
                     sign_off="Head of comms"),
    "policy": Persona(key="policy", who="Policy advisor", action="brief a legislator with it",
                      sign_off="Policy director"),
    "vc": Persona(key="vc", who="VC associate", action="cite it in an investment memo", sign_off="Partner"),
}

# Default persona per report (any persona can be used with any report).
DEFAULT_FOR_REPORT = {"ev_market": "analyst", "wfh": "comms", "nuclear": "policy", "agi": "vc"}


def persona_for(report_name: str) -> Persona:
    return PERSONAS[DEFAULT_FOR_REPORT.get(report_name, "analyst")]
