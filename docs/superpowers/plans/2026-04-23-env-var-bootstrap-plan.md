# Env Var Bootstrap Pattern Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Change `load_config()` so env vars are used only on first boot (when DB doesn't exist), then database is the sole source of truth thereafter.

**Architecture:** `load_config()` checks if the SQLite database file exists. If no DB: use env vars, write config.json, return config. If DB exists: return config.json only (env vars ignored).

**Tech Stack:** Python, SQLite

---

## File Map

| File | Action | Purpose |
|---|---|---|
| `run.py` | Modify | Change `load_config()` to check DB existence first |

---

## Task 1: Modify load_config() Bootstrap Logic

**Files:**
- Modify: `run.py:144-148`

**Changes:** Replace `load_config()` with new bootstrap-aware version that:
1. Determines DB path from env var or default
2. Checks if DB file exists
3. If DB exists: return config.json only (ignore env vars)
4. If no DB: use env vars, write config.json, return config

- [ ] **Step 1: Read current load_config()**

The current `load_config()` is at lines 144-148:

```python
def load_config() -> dict:
    """Load config.json, then overlay env vars for Docker overrides."""
    cfg = _load_config_file()
    env_cfg = _env_to_config()
    return _merge_configs(cfg, env_cfg)
```

- [ ] **Step 2: Replace load_config() with bootstrap-aware version**

Replace the `load_config()` function with:

```python
def load_config() -> dict:
    """Load configuration.

    Bootstrap behavior:
    - If DB doesn't exist: use env vars to build config, write config.json, return config.
    - If DB exists: return config.json only (env vars are ignored after first boot).
    """
    db_path = os.environ.get("DATABASE_PATH", "smtp_relay.db")
    if not os.path.isabs(db_path):
        db_path = os.path.join(SCRIPT_DIR, db_path)

    if os.path.exists(db_path):
        return _load_config_file()

    env_cfg = _env_to_config()
    if os.path.exists(CONFIG_FILE):
        file_cfg = _load_config_file()
        cfg = _merge_configs(file_cfg, env_cfg)
    else:
        cfg = env_cfg

    with open(CONFIG_FILE, 'w') as f:
        json.dump(cfg, f, indent=4)

    return cfg
```

- [ ] **Step 3: Commit**

```bash
git add run.py && git commit -m "feat: bootstrap config from env vars only when DB doesn't exist"
```

---

## Verification

After task complete:

1. **First boot test (no DB):**
   ```bash
   rm -f smtp_relay.db
   DATABASE_PATH=smtp_relay.db SECRET_KEY=test123 python -c "from run import load_config; cfg = load_config(); print('DB-less config loaded:', cfg.get('delivery_mode', {}).get('mode'))"
   ```
   Should create config.json and return config.

2. **Subsequent boot test (DB exists):**
   ```bash
   python -c "from run import load_config; cfg = load_config(); print('DB config loaded, mode:', cfg.get('delivery_mode', {}).get('mode'))"
   ```
   Should NOT read SECRET_KEY from environment.

3. **Push to beta repo:**
   ```bash
   git push beta main
   ```

---

## Spec Coverage Check

| Spec Requirement | Task(s) |
|---|---|
| Env vars on first boot only | Task 1 |
| config.json written once | Task 1 |
| DB as sole authority after first boot | Task 1 |

**Placeholder scan:** None found.
**Type consistency:** `load_config()` signature unchanged.
