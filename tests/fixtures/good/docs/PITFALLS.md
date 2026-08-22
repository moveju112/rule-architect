# Pitfalls

## Symptom: `RuntimeError: settings not loaded`

Cause: settings were imported before the env loader ran.
Fix: follow the env-ordering rule in [docs/CODING_RULES.md](docs/CODING_RULES.md).

## Symptom: connection pool exhausted after an hour

Cause: a second engine was constructed outside the factory.
Fix: reuse `src/db.py:3`.
