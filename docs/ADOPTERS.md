# Who Cortex Is For

Cortex is for builders who want agent capability with runtime controls.

## Good fits

- Developer teams evaluating AI agents that can touch code.
- Internal automation where every material action needs a log.
- Security-conscious local-first experiments.
- Mobile/PWA demos that need simple auth and visible state.
- Research on constrained action languages and agent governance.

## Not a good fit yet

- Nontechnical users expecting a polished chatbot.
- Workloads requiring a certified sandbox or formal compliance attestation today.
- Fully autonomous production changes without human review.

## Core promise

Cortex separates proposal from authority:

```text
model proposes -> runtime validates -> policy gates -> verifier checks -> audit records
```

The runtime can refuse, budget, roll back, and leave evidence.

## First demo to show someone

1. Run the tests.
2. Start `python -m cortex.web`.
3. Open `/mobile`.
4. Show `/oauth/status`.
5. Ask a question using `@{LAW.md}` file context.
6. Show the audit sink docs in `docs/soc2/audit_sink.md`.

That demonstrates plug-and-play setup, auth posture, bounded context, and auditability.
