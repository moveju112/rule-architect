# Coding Rules

- **[MUST]** load env before importing settings
  - why: settings read os.environ at import time, so a late load yields empty values
  - ❌ `import config` at the top of a script that loads env later
  - ✅ `src/config.py:1` is imported only after `load_project_env()` runs
- **[NEVER]** create an engine outside the factory
  - why: two engines mean two pools and a silent connection leak
  - ✅ `src/db.py:3` returns the single shared engine
