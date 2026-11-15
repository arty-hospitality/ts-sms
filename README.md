# TenantSage Governance Control Plane (ts_sms)
Version: 1.0 (FROZEN) Classification: STRICT ISOLATION / SECURE ENCLAVE
Last Updated: July 12, 2026

## Overview
The ts_sms (TenantSage State Machine & Security) 
module is the governance core of the TenantSage architecture. 
It acts as the sole arbiter of authority for document-bounded AI retrieval.
This module is strictly isolated from the application logic, user interface, 
and standard RAG pipelines. Its singular purpose is to enforce the Dual-Hierarchy (Organizational + Data) constraints, 
construct the Evidence Enforcement Boundary (EEB), and maintain a cryptographic provenance ledger of all execution cycles.

CRITICAL: The application layer must never attempt to bypass this control plane. 
All requests for AI retrieval must pass through the interface/gateway.py.

## Architecture Principles
Identity $\neq$ Authority: Authentication merely establishes identity. 
Authority is dynamically derived via the Dual-Authority Resolver (DAR) based on temporal, 
relationship-based access control (ReBAC) assignments.

Authority Precedes Retrieval (Law 10): No vector search or similarity computation can occur until the DAR has fully resolved 
the organizational and data scopes.Deterministic Boundaries: The intersection of the organizational and data scopes forms 
the Evidence Enforcement Boundary (EEB). Only documents within this strict mathematical boundary are eligible for retrieval.

Cryptographic Provenance: Every execution cycle is anchored to an append-only ledger via a ProvenanceChain, 
linking cryptographic hashes across all stages of the pipeline.Fail-Closed (Law 29): Any exception, timeout, 
or failure at any state in the pipeline physically destroys the execution context and triggers a formal failure state (SF_AUTH, SF_EEB, etc.), 
returning a NULL_HASH for provenance.

## Implementation Status (v1.0 runtime in this repository)

| Component | Status |
|---|---|
| S0 -> S1 -> S3 -> S11/S12 workflow, Law 29 fail-closed, Law 25 genesis | Implemented |
| DAR (S1) | Implemented against a JSON assignment store (see *Assignment store*); fails closed when the store is missing, malformed, or holds no unique active assignment. Not yet a full ReBAC service. |
| Ledger | Implemented: ALLOW and DENY entries, optional HMAC keying |
| S6 Context Governance Layer (CGL) | **Not implemented** (pass-through state); no prompt-inspection policy is defined or enforced |
| Response Validation Layer (RVL) | **Not implemented** |
| `formal_methods/` (TLA+/Alloy models) | **Not in this repository**; the docstrings that cite them refer to models that are not shipped or checked here |
| `contracts/` (Protobuf/JSON schemas) | **Not in this repository**; the dict shapes in code are the de-facto contracts |
| `.seal` cryptographic release signature | **Not present**; `.seal` describes the target layout only |

Directory Structure Guideinterface/: The sole entry point for the external RAG application (gateway.py).

state_machine/: The Temporal workflow (engine.py) that executes the deterministic S0 -> S12 transition.

dar/: The Dual-Authority Resolver, computing organizational and data scopes.

eeb/: Constructs the deterministic query filters from the DAR scopes.

ledger/: Manages the cryptographic append-only evidence ledger.

laws/: Hard-coded enforcement of the TS-SMS constitution (e.g., Fail-Closed).

contracts/: (planned, not present) Protobuf/JSON schema definitions defining the boundaries between states.

crypto/: Core hashing and canonical serialization primitives.

Operator Notes & ARB RulingsRuling 2026-07-08: The state machine guarantees unconditional safety. 

Progress is proven only under explicit availability assumptions (fault tolerance is not guaranteed, but fail-closed behavior is absolute).

TS-API-009: Defined strictly as "Generation Execute." Authority resolution is upstream (S1-S3).

Compliance: Compliance is a cross-cutting concern managed by the "Admin" tier and supported by the Evidence Ledger, not a distinct hierarchy tier.

Genesis Integrity: The Genesis Snapshot must exist at system bootstrap (Law 25).
Usage (Application Integration)The application layer interacts with the control plane solely through the Temporal workflow client. 

from ts_sms.interface.gateway import TenantSageGateway

# Initialize the gateway
gateway = TenantSageGateway(temporal_target="localhost:7233")

# Submit a request (The app halts until a terminal state is reached)
result = await gateway.execute(
    user_id="user_123",
    tenant_id="tenant_A",
    query="Summarize the Q3 financial reports."
)

if result["status"] == "ALLOW":
    # Proceed with constrained vector search using result["eeb"]
    pass
else:
    # Handle formal failure state (e.g., result["failure_state"] == "SF_AUTH")
    pass

## Running the Service

The control plane ships as the `ts_sms` Python package plus a Temporal worker
(`ts-sms-worker`) that hosts the `TenantSageStateMachine` workflow and its
activities (`resolve_dual_authority`, `build_evidence_boundary`,
`commit_to_ledger`, `record_denial`) on the `ts-sms-governance-queue` task queue.

```
ts_sms/
├── config.py                 # Environment-driven settings + Temporal client factory
├── worker.py                 # ts-sms-worker entrypoint
├── interface/gateway.py      # TenantSageGateway.execute()
├── interface/cli.py          # ts-sms-execute (submit one request from the shell)
├── state_machine/engine.py   # TenantSageStateMachine workflow (S0 -> S12)
├── dar/resolver.py           # resolve_dual_authority activity (S1)
├── dar/assignments.py        # time-bounded assignment store (fail-closed loader)
├── eeb/builder.py            # build_evidence_boundary activity (S3)
├── ledger/chain.py           # commit_to_ledger / record_denial activities + ProvenanceChain
├── ledger/auditor.py         # verify_chain (H_prev linkage / hash integrity)
├── laws/law_29_fail_closed.py
└── crypto/                   # canonical JSON, SHA-256/HMAC derivation, payload encryption codec
```

### Install

```bash
pip install .            # runtime (pins temporalio)
pip install -e '.[dev]'  # + pytest
pytest                   # unit tests + workflow tests on Temporal's in-process test server
```

### Configuration

| Variable | Default | Purpose |
|---|---|---|
| `TS_SMS_TEMPORAL_ADDRESS` | `localhost:7233` | Temporal frontend `host:port` (gateway + worker) |
| `TS_SMS_TEMPORAL_NAMESPACE` | `default` | Temporal namespace |
| `TS_SMS_TASK_QUEUE` | `ts-sms-governance-queue` | Task queue the worker polls and the gateway targets |
| `TS_SMS_TEMPORAL_TLS` | `false` | Enable server-auth TLS (implied when an API key is set) |
| `TS_SMS_TEMPORAL_TLS_CLIENT_CERT` / `TS_SMS_TEMPORAL_TLS_CLIENT_KEY` | unset | Paths to an mTLS client cert/key (e.g. Temporal Cloud mTLS) |
| `TS_SMS_TEMPORAL_API_KEY` | unset | Temporal Cloud API key |
| `TS_SMS_ASSIGNMENTS_PATH` | unset (**every request is denied**) | JSON assignment store read by the DAR (worker only) |
| `TS_SMS_LEDGER_HMAC_KEY` | unset | Key ledger entry hashes with HMAC-SHA256; a keyed worker refuses an unkeyed ledger (worker only) |
| `TS_SMS_DIGEST_KEY` | unset | HMAC key for the query digest (gateway) and principal digest (worker); without it plain SHA-256 is used, which a dictionary attack can invert for short queries |
| `TS_SMS_PAYLOAD_KEY` | unset | Base64 32-byte AES-256-GCM key; encrypts every Temporal payload. Gateway and worker must use the same key |
| `TS_SMS_LEDGER_PATH` | `ts_sms_ledger.jsonl` (`/var/lib/ts-sms/ledger.jsonl` in Docker) | Append-only provenance ledger file (worker only) |
| `TS_SMS_WORKFLOW_TIMEOUT_SECONDS` | `60` | Gateway-side workflow execution timeout; on expiry the gateway returns `SF_GOV` |
| `TS_SMS_LOG_LEVEL` | `INFO` | Worker log level |

`TenantSageGateway(temporal_target=...)` still accepts an explicit address, which overrides `TS_SMS_TEMPORAL_ADDRESS`.

### Assignment store

The DAR allows a request only when exactly one assignment for the `(user_id, tenant_id)`
pair is active at the workflow's own clock (`workflow.now()`); the caller cannot supply
the evaluation time. Organizational and data scope come from that assignment. See
[`examples/assignments.example.json`](examples/assignments.example.json):

```json
{"assignments": [{"user_id": "user_123", "tenant_id": "tenant_A", "org_node_id": "child_node_tenant_A",
  "level": "Child", "role": "analyst", "data_classification": "CONFIDENTIAL",
  "effective_from": "2026-01-01T00:00:00Z", "effective_to": null}]}
```

Every field is required (`effective_to: null` means open-ended); times must be ISO-8601 with a timezone.

### Run locally with Docker Compose

Create a `.env` next to `docker-compose.yml` (compose refuses to start without the first two):

```bash
TEMPORAL_DB_PASSWORD=<strong random password>
TS_SMS_ASSIGNMENTS_FILE=./examples/assignments.example.json   # replace with your store
# optional: TS_SMS_LEDGER_HMAC_KEY / TS_SMS_DIGEST_KEY / TS_SMS_PAYLOAD_KEY
```

```bash
docker compose up -d --build     # Postgres + Temporal (auto-setup) + Temporal UI + ts-sms worker
docker compose logs -f worker    # wait for "TS-SMS worker polling task queue ..."
```

Temporal (`7233`) and the Temporal UI (`8080`) are bound to `127.0.0.1` only. This dev
Temporal has no TLS, authentication or namespace authorizer: anything that can reach port
7233 can start workflows, read history, or poll the task queue with its own worker and
return a forged result. Do not widen these bindings; in production use a Temporal cluster
with mTLS/API-key auth. The ledger lives in the `ts-sms-ledger` volume.

### Run the worker without Docker

```bash
export TS_SMS_TEMPORAL_ADDRESS=localhost:7233
ts-sms-worker                    # or: python -m ts_sms.worker
```

On startup the worker writes the Genesis Snapshot if the ledger is empty (Law 25),
verifies the whole chain, and refuses to start if the ledger has been tampered with.
It shuts down gracefully on `SIGTERM`/`SIGINT`.

### Submit a request

From the shell:

```bash
ts-sms-execute --user-id user_123 --tenant-id tenant_A --query "Summarize the Q3 financial reports."
# inside compose:
docker compose run --rm worker ts-sms-execute --user-id user_123 --tenant-id tenant_A --query "..."
```

From the application:

```python
import asyncio
from ts_sms.interface.gateway import TenantSageGateway

async def main():
    gateway = TenantSageGateway()  # reads TS_SMS_* env vars
    result = await gateway.execute(user_id="user_123", tenant_id="tenant_A", query="Summarize Q3.")
    print(result["status"], result.get("provenance", {}).get("provenance_hash"))

asyncio.run(main())
```

An `ALLOW` result carries `eeb` (filters + `eeb_hash`) and `provenance`
(`ledger_index`, `h_prev`, `provenance_hash`, ...). Any failure returns a
TS-CONTRACT-011 `DENY` with `failure_state` (`SF_AUTH`, `SF_EEB`, `SF_LEDGER`, `SF_GOV`)
and `NULL_HASH` provenance, and is itself ledgered as a `DENY` entry. Internal error
text is logged, never returned. Activities retry at most 3 times before Law 29 fires;
validation and integrity errors (`ValueError`, `AssignmentStoreError`,
`LedgerIntegrityError`, ...) are not retried.

### What reaches Temporal history

The gateway sends only `user_id`, `tenant_id` and `query_hash` (a digest of the query,
keyed with `TS_SMS_DIGEST_KEY` when set). Workflow ids are random and carry no identifiers.
Inputs and results are kept in history for the namespace's configured retention period;
set `TS_SMS_PAYLOAD_KEY` to encrypt them at rest.

### Deploy to production

1. Build and push the image: `docker build -t <registry>/ts-sms-worker:<tag> . && docker push ...`
2. Point it at your Temporal cluster (self-hosted or Temporal Cloud) with
   `TS_SMS_TEMPORAL_ADDRESS`, `TS_SMS_TEMPORAL_NAMESPACE` and either
   `TS_SMS_TEMPORAL_API_KEY` or the mTLS cert/key variables.
3. Mount a persistent volume at `/var/lib/ts-sms` for the ledger.
4. Provide the assignment store (`TS_SMS_ASSIGNMENTS_PATH`) and, for production,
   `TS_SMS_LEDGER_HMAC_KEY`, `TS_SMS_DIGEST_KEY` and `TS_SMS_PAYLOAD_KEY` from a secret manager.
5. The bundled `docker-compose.yml` is a development stack (unauthenticated Temporal,
   tag-pinned images); do not use it as a production deployment.

### Ledger integrity limits

- Without `TS_SMS_LEDGER_HMAC_KEY`, anyone with write access to the ledger file can
  rewrite it and recompute a chain that still verifies. With the key, they cannot without
  also holding the key, so keep it out of the ledger volume.
- Neither mode detects deletion of the newest entries (tail truncation). Detecting that
  requires periodically anchoring the latest `entry_hash` outside the ledger (e.g. a WORM
  store or transparency log); this is not implemented.
- Each append is `flock`ed and `fsync`ed, which makes a committed line durable but does
  not make it atomic. A crash can leave a torn final line; the worker then refuses to
  append or start. Preserve the file as evidence, and reconcile the torn entry against
  Temporal workflow outcomes before repairing it manually.
- Ledgers written before v2 entries (no `v` field) still verify and are extended with v2
  entries; an unkeyed ledger cannot be converted to HMAC mode in place.

The file ledger uses `flock`, so several worker processes may share it on one host/volume.
It does not support workers on different hosts writing the same chain; for horizontal
scale-out, back `ProvenanceChain` with a shared transactional store.
