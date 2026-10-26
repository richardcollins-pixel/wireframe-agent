# Wireframe agent

Generates three wireframe directions from a brief, lets people comment at any level, and narrows to one prototype.

- `server.py`: local backend. Reads the design system from a design-system checkout, sends it to the Pipedream copy of the generator, and polls for results.
- `.env.example`: settings. Copy to `.env` (git-ignored) and fill in.

Run: `python3 server.py`
