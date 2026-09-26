"""Batch-06 / LLM-001..003: local model explanation layer.

Security invariants (Freeze 5):
- Model output NEVER mutates the deterministic graph or analysis state.
- Claims must reference evidence IDs that exist in the SAME analysis;
  hallucinated or cross-analysis references fail validation.
- The local Ollama provider may only talk to a loopback endpoint (never
  LAN/public); the remote provider requires a per-analysis one-time consent
  and may only talk to public endpoints (never loopback/private/reserved).
- No tool calling: prompts are plain data, provider output is parsed as
  JSON and validated before persistence.
"""
