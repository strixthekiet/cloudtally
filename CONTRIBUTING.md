# Contributing to CloudTally

Thanks for your interest in contributing!

## Development setup

```bash
git clone <your-fork>
cd cloudtally
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[gcp,dev]"
pytest
```

Demo mode needs no credentials, so `cloudtally run` and the test suite work
out of the box.

## What contributions look like

- **New pricing mappers** (the most useful kind): teach
  `cloudtally/providers/gcp/mapper.py` a new asset type and add the matching
  SKUs to the seed catalog. Every resource must carry an honest confidence
  level — when real cost depends on usage that inventory cannot see, flag it
  `usage_based` or `estimated` with a note rather than guessing.
- **New providers**: implement `CloudProvider`
  (`cloudtally/providers/base.py`) — an inventory source, a pricing source
  normalized to catalog keys, and a mapper. The engine, store, and CLI are
  provider-agnostic.
- **Fixes and tests**: bug reports with a failing test are gold.

## Ground rules

- Python 3.10+, standard library first; core dependencies are deliberately
  minimal (PyYAML only — cloud SDKs live behind the `gcp` extra and are
  imported lazily).
- Match the existing style; keep modules small and focused.
- Add or update tests for behavior changes, and run `pytest` before opening
  a PR.
- One logical change per pull request, with a clear description of the why.

## Reporting issues

Open a GitHub issue with the command you ran, expected vs. actual output,
and `cloudtally validate` output (redact project IDs if needed).
