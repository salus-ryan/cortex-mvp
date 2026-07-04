# Tamper-Evident Audit Sink

Cortex records runtime security events to a local JSONL audit sink with a SHA-256 hash chain.

Implementation:

- `cortex/audit_sink.py`
- integrated into `CortexRuntime`
- default path: `ledger/audit.jsonl`

Each event includes:

- `event_id`
- `timestamp`
- `task_id`
- `step`
- `actor`
- `action`
- `decision`
- `event_type`
- `data`
- `previous_hash`
- `hash`

Verification:

```python
from cortex.audit_sink import AuditSink
print(AuditSink("ledger/audit.jsonl").verify())
```

This is tamper-evident, not tamper-proof. It detects local modification, removal, or reordering of events in the hash chain. For stronger SOC 2 evidence, export these logs to external append-only storage.
