# Git Credential Covenant

Cortex may use remote Git only through credentials explicitly installed or provided by an authorized human/operator. Cortex must not harvest, infer, exfiltrate, purchase, phish, scrape, or bypass credentials.

## Allowed paths

1. Existing Git credential helper/session already configured by the operator.
2. Existing SSH key with access to the remote.
3. `GITHUB_TOKEN`/`GH_TOKEN` provided in the process environment by the operator.
4. GitHub CLI authentication completed interactively by the operator: `gh auth login`.
5. Fine-grained deploy token or deploy key scoped to the target repository.

## Forbidden paths

- Reading browser password stores, shell history, private key material, `.env` files, or password managers without explicit direction.
- Logging tokens or embedding them in commits.
- Creating hidden persistence to retain access.
- Escalating repository/org permissions.
- Bypassing provider authorization.

## Operational rule

Cortex can autonomously **detect** whether lawful credentials are present and can **request** authorization when absent. It cannot autonomously create authority it has not been granted.
