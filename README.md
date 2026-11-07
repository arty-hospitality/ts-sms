# TenantSage Governance Control Plane (ts_sms)
Version: 1.0 (FROZEN) Classification: STRICT ISOLATION / SECURE ENCLAVE
Last Updated: July 12, 2026

## Overview
The ts_sms (TenantSage State Machine & Security) 
module is the immutable, mathematically verified core of the TenantSage architecture. 
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

Formal VerificationThe logic within ts_sms is backed by rigorous formal methods (see the formal_methods/ directory):
TLA+ (authoritycontrolplatform.tla, allowderivedcanonical.tla): 
Proves behavioral properties, such as the unconditional safety of the state machine, strict ledger append ordering, 
and the integrity of the provenance lineage (parentSource).Alloy (ALLOY_MODEL.als): Proves structural invariants, 
ensuring that no evidence can exist outside the computed EEB and that cross-tenant assignment is structurally impossible.

Directory Structure Guideinterface/: The sole entry point for the external RAG application (gateway.py).

state_machine/: The Temporal workflow (engine.py) that executes the deterministic S0 -> S12 transition.

dar/: The Dual-Authority Resolver, computing organizational and data scopes.

eeb/: Constructs the deterministic query filters from the DAR scopes.

ledger/: Manages the cryptographic append-only evidence ledger.

laws/: Hard-coded enforcement of the TS-SMS constitution (e.g., Fail-Closed).

contracts/: The Protobuf/JSON schema definitions defining the boundaries between states.

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
`commit_to_ledger`) on the `ts-sms-governance-queue` task queue.

```
ts_sms/
├── config.py                 # Environment-driven settings + Temporal client factory
├── worker.py                 # ts-sms-worker entrypoint
├── interface/gateway.py      # TenantSageGateway.execute()
├── interface/cli.py          # ts-sms-execute (submit one request from the shell)
├── state_machine/engine.py   # TenantSageStateMachine workflow (S0 -> S12)
├── dar/resolver.py           # resolve_dual_authority activity (S1)
├── eeb/builder.py            # build_evidence_boundary activity (S3)
├── ledger/chain.py           # commit_to_ledger activity + ProvenanceChain (S11 -> S12)
├── ledger/auditor.py         # verify_chain (H_prev linkage / hash integrity)
├── laws/law_29_fail_closed.py
└── crypto/                   # canonical serialization + SHA-256 derivation
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
| `TS_SMS_LEDGER_PATH` | `ts_sms_ledger.jsonl` (`/var/lib/ts-sms/ledger.jsonl` in Docker) | Append-only provenance ledger file (worker only) |
| `TS_SMS_WORKFLOW_TIMEOUT_SECONDS` | `60` | Gateway-side workflow execution timeout; on expiry the gateway returns `SF_GOV` |
| `TS_SMS_LOG_LEVEL` | `INFO` | Worker log level |

`TenantSageGateway(temporal_target=...)` still accepts an explicit address, which overrides `TS_SMS_TEMPORAL_ADDRESS`.

### Run locally with Docker Compose

```bash
docker compose up -d --build     # Postgres + Temporal (auto-setup) + Temporal UI + ts-sms worker
docker compose logs -f worker    # wait for "TS-SMS worker polling task queue ..."
```

Temporal is exposed on `localhost:7233` and the Temporal UI on http://localhost:8080.
The ledger lives in the `ts-sms-ledger` volume.

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
and `NULL_HASH` provenance. Activities retry at most 3 times before Law 29 fires.

### Deploy to production

1. Build and push the image: `docker build -t <registry>/ts-sms-worker:<tag> . && docker push ...`
2. Point it at your Temporal cluster (self-hosted or Temporal Cloud) with
   `TS_SMS_TEMPORAL_ADDRESS`, `TS_SMS_TEMPORAL_NAMESPACE` and either
   `TS_SMS_TEMPORAL_API_KEY` or the mTLS cert/key variables.
3. Mount a persistent volume at `/var/lib/ts-sms` for the ledger.
4. The bundled `docker-compose.yml` can also be used for a single-host deployment;
   set `TEMPORAL_DB_PASSWORD` and avoid publishing ports 7233/8080 publicly.

The file ledger uses `flock`, so several worker processes may share it on one host/volume.
It does not support workers on different hosts writing the same chain; for horizontal
scale-out, back `ProvenanceChain` with a shared transactional store.
