## Summary

<!-- What does this PR do, and why? One or two sentences. -->

## Changes

<!-- Bullet the concrete changes. Note any new migrations, endpoints, or engine rules. -->

-
-

## Testing

<!-- How did you verify this? Paste the relevant commands/output. -->

- [ ] Backend tests pass — `cd backend && pytest`
- [ ] Frontend builds and tests pass — `cd frontend && npm run build && npm test`
- [ ] AI eval still reports expected metrics (if AI layer touched) — `python -m app.ai.eval.run_eval`
- [ ] New Alembic migration round-trips (`upgrade → downgrade`), if schema changed

## Checklist

- [ ] Routers stay thin; business rules live in the engine layer
- [ ] No real secrets or non-synthetic data added (`git ls-files | grep -i secret` is clean)
- [ ] Docs / README updated if behavior changed
- [ ] Linked the relevant issue (`Closes #...`)
