# Architecture

- `src/config.py` — settings loader
- `src/db.py` — engine factory
- `Dockerfile` — runtime image

Data flow: collector reads upstream, normalizes, writes through the engine.
