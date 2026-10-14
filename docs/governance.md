# Governance resources

Camayoc provides an explicit offline stage for the governance catalogue and
combined SHACL profile. It writes a new owned scratch database only after native
pendant verification and exact catalogue/profile readback. It does not load a
running service, replace an existing catalogue, install a native policy adapter
or activate a hook.

See the [resource guide](../governance/README.md) for required identity arguments,
repeat behavior and refusal cases. The [consumer contract](design/governance-consumer-contract.md)
defines the separate projection, cache, exemption, freshness and atomic refresh
requirements for a future native adapter. Offline stage success does not prove
that adapter is implemented or independently certified.

Tests generate a fresh local pendant from the reviewed resource bytes, verify
449 declarations and the complete profile, and exercise an isolated server:
unknown types and invalid mixed transactions leave every submitted subject
absent; a valid proposed AdmissionPolicy is the positive writer control.
