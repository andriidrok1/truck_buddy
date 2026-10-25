"""Service layer. Each module exposes a provider interface plus a MOCK implementation.

Swap the mock providers for real ones (Overpass, OSRM, Whisper + GPT + TTS) by
replacing the instances returned from the `get_*_provider()` functions.
"""
