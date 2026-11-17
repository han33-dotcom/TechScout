# Monitor and audio identity

Task #44 extends the existing v2 `compare_offers`, `variant_identity_key` and
`group_variants` functions. It preserves phone/laptop keys and does not change
wire contracts. Tablet identity remains the separately assigned #43.

Monitor equivalence requires sourced manufacturer/model, color, exact diagonal,
width/height resolution, refresh rate and panel. Audio equivalence requires
sourced manufacturer/model including its published version, color, form factor,
connectivity and noise-cancellation state. Optional fields that are absent remain
unresolved for equivalence; a known decisive difference can still identify a
different configuration. Neither category acquires phone SIM/storage rules.

Model/version strings retain suffixes, punctuation and numeric boundaries. Only
case, whitespace and typographic dash differences normalize. Generic descriptions
such as “wireless headphones” and explicit ambiguous labels such as “X or Y” or
“X series” cannot prove a match. Extraction must provide the exact sourced model
and generation/version: identity does not infer a missing generation, consult a
model catalog or manufacture specifications. Alphabetic product model names are
supported. Unknown/conflicting facts and missing model/manufacturer receipts
remain unresolved; source SKUs and canonical IDs cannot override them.

Seller condition, payment, activation when supplied, and warranty remain separate
from hardware identity. Equivalence is limited to the enumerated profile, not
all physical attributes or purchase terms. Exact decimal sizes are hashed without
rounding under the caller's decimal context. Model changes, configuration changes
and unresolved records retain distinct seller groups.

Synthetic fixtures and tests cover both categories, cross-seller equivalents,
near-name/version collisions, size/resolution/refresh differences, audio form and
connectivity differences, unknown/conflicting fields, invalid types, absent
evidence, purchase-term differences and decimal precision. No retailer claims or
live retrieval are implied.
