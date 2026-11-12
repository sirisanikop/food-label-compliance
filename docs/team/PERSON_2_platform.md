# Person 2 - Platform and API

You own the shared foundation, so you go first: everyone else's code imports `common/`.

**Requirements:** F1, F2, F8, A4, A8, D1, D3, D4 (and the repository README, which is tested by running it unassisted).

## Week-1 priority (a working end-to-end path)

1. Create the GitHub repo with an empty `main` (no README/gitignore from GitHub) and push your files first.
2. Everyone else pulls, adds their files, pushes.
3. Run `docker compose up --build` from a clean clone and get a valid request through. Fix what breaks, and tell the owner of the file that broke.

## Then

- Make `README.md` sufficient for a stranger. Test it on a machine that has never seen the project.
- Check no credentials, endpoints or model names are in the repo outside `.env.example` (D4). Add a CI-style check script if you like.
- Think about whether the boundaries in `docs/deployment_boundaries.md` are really the ones you would defend in the viva.
- Add a callback option or a `Retry-After` hint on polling if you want to go beyond polling (A4 asks for justification, not both).
- Own `docs/report_outline.md` if you want a head start on the report: problem and scope, agent design, architecture diagram with boundaries, decisions and rejected options, evaluation before/after, SLO/bottleneck/cost, what did not work.

## Files

`src/foodlabel/api/`, `src/foodlabel/common/`, `services/api/`, `services/web/`, `docker-compose.yml`, `.env.example`, `.gitignore`,
`Makefile`, `pyproject.toml`, `requirements-dev.txt`, `contracts/`, `examples/`, `scripts/`, `README.md`, `tests/conftest.py`, `tests/api/`,
and the docs listed in `OWNERSHIP.md`.
