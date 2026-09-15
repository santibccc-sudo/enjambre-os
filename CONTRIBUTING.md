# Contributing

Thanks for helping. A few things keep enjambre small and honest.

## Setup

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest
enjambre demo
```

## Principles

1. **Every rule comes from a failure.** New mechanisms should come with the incident or the
   failing test that motivates them.
2. **State is derived, not declared.** If a component reports its own health, the kernel
   should be able to check it independently.
3. **Nothing blocks forever.** Every lock, lease or wait needs an expiry and a visible reason
   when it gives up.
4. **Measured, never invented.** Missing data is `None`, not zero.
5. **Small dependencies.** The core stays on the standard library plus PyYAML. Optional
   integrations belong behind adapters.

## Pull requests

- Add a test that fails without your change.
- Keep the dashboard free of `innerHTML` with data and free of inline scripts.
- Run `pytest` before opening the PR. CI also runs a secret scan.
- Describe the user-visible behaviour change in the PR description.
