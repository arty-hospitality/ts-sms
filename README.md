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

## RAG Data Hierarchy: rag_fact
The data hierarchy is `family → parent → children → assignment → rag_source → rag_chunk → rag_fact`.

`rag_fact` is an optional structured fact record that enables precise key-based lookups
(e.g. "What is the supervisor threshold?") so retrieval does not rely solely on semantic similarity.
Facts supplement, and never replace, chunk/semantic retrieval; the absence of facts never fails the flow.

Each fact carries only identity, linkage and lookup fields:
`rag_fact_id`, `rag_source_id` (pointer to its mother `rag_source`),
`fact_type` (`threshold`, `time_standard`, `role_policy`, `override_rule`, `glossary`), `key`, and a JSON `value`.

Like `rag_chunk`, a `rag_fact` carries lineage, not independent permission state. Its eligibility and governance
(`effective_from`/`effective_to`, role visibility, retention, RBAC confirmed in the ledger) are inherited from its
parent `rag_source`. If the parent source is ineligible or cannot be resolved, the fact is DENY (fail-closed).
Fact-level governance fields from the legacy Family/Child schema (`visibility`, `effective_from`/`effective_to`,
`family_id`/`child_id`) are not authoritative. The EEB confines facts by membership in the set of eligible
`rag_source` ids, and the fact→source linkage is bound into the governance `context_hash`.

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
