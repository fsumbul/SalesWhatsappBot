"""Input guardrail gate (plan WP1): classify, never answer.

Customer messages are checked before any model call; document chunks before
extraction and indexing. Verdicts are data for trusted code — a blocked turn
renders approved fallback text, a blocked chunk is simply not processed.
"""
