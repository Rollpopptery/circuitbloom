"""
discipline.py — Routing discipline generator.

Returns a random high-level prompt to reset focus during routing.
Call this when stuck, after failures, or periodically between routes.

Usage:
    from discipline import prompt
    print(prompt())
"""

import random

PROMPTS = [
    "Have you tried vias? One F.Cu failure = switch to B.Cu.",
    "Check component positions. Can anything move to make this easier?",
    "Run viacheck. Every via on the board, right now.",
    "Look at the waypoints of the last trace. Is it blocking a corridor?",
    "Routing order matters. Should a different net go first?",
    "Are you using margin=3 on every route command?",
    "Fan-out: is the stub perpendicular to the pin row? Inner short, outer long?",
    "How many iterations did the failure have? <500 = boxed in, unroute neighbour. >500 = try B.Cu.",
    "Probe around the pad. What's actually blocking?",
    "Stop. Is this trace going to block other pins? Count the foreign pads near it.",
    "You have two layers. Use both.",
    "Is this workaround simpler than just unrouting the blocker?",
    "Check the connector pin order. Are you routing them in the right sequence?",
    "How many attempts on F.Cu so far? If more than one, use vias.",
    "Will this trace cut the board in half? Think about what still needs to cross.",
]


def prompt():
    """Return a random discipline prompt."""
    return random.choice(PROMPTS)
