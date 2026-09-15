# Security

## Reporting a vulnerability

Please report vulnerabilities privately through GitHub's **Report a vulnerability** button on
this repository (Security → Advisories). Do not open a public issue. We aim to acknowledge
reports within a week.

## Threat model and defaults

enjambre coordinates processes that can run commands and call models, so treat a swarm like
any other automation with access to your machine.

- **Network exposure.** `enjambre up` binds to `127.0.0.1`. Binding to any other address
  requires `ENJAMBRE_TOKEN` (or an explicit `--insecure`). Tokens are compared in constant time.
  Put a TLS-terminating proxy in front of a swarm you expose beyond a trusted network.
- **Agents are untrusted.** Task titles, results and memory notes are rendered as text in the
  dashboard, never as HTML. Upstream results are labelled as data, not instructions, in every
  prompt.
- **CLI agents** run without a shell. They only receive `PATH`, `HOME`, locale variables and
  the variables listed in `options.env`.
- **API keys** are read from environment variables. `swarm.yaml` files that contain an
  `api_key` are rejected.
- **Proof checks** read file metadata and send `HEAD`/`GET` requests to URLs that agents
  declare. If agents could point them at internal services you care about, set
  `kernel.allow_url_proof: false` in `swarm.yaml`.
- **Policy** fails closed: if no valid `policy.yaml` was ever loaded, gated dispatches are
  refused. A broken edit keeps the last good policy.
- **Dashboard** is served with a strict Content Security Policy, `nosniff` and no referrer.
- **The SQLite file** contains task details and results. Keep it out of version control
  (the default `.gitignore` does) and protect it like any other application data.
