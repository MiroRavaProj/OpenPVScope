# Contributing to OpenPVScope

## Dev setup

1. Python 3.10+ and Node 18+
2. Backend: `cd backend && pip install -e ".[dev]"`
3. Frontend: `cd frontend && npm install && npm run dev`
4. API: `uvicorn openpvscope.api.app:app --reload --port 8787`

## Project rules

- Keep pipeline logic in pure Python (`openpvscope/`) — no UI imports.
- Persist stage data under the `.opsx` layout (see `docs/opsx_format.md`).
- Prefer small, focused PRs.

## Tests

```bash
cd backend
pytest
```
