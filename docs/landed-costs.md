# Historical international landed costs

The fixture-based [landed-cost engine](../app/landed_costs.py) calculates direct delivery and forwarding with exact decimal arithmetic. The current buyer contract uses Lebanese merchants and their quoted fees. [Domestic costs](full-product-assessment.md) · [Buyer contract](buyer-product-contract.md)

## Legacy interface

~~~python
from app.assessment import assess_full_product, shortlist
from app.landed_costs import RouteInputs, calculate_landed_cost

landed = calculate_landed_cost(request, offer, RouteInputs(...), fx=rates, now=now,
                               run_started_at=run_started)
# landed.cost: the v2 cost; landed.components: every charge slot; landed.support: route verdict

assessment = assess_full_product(request, offer, fx=rates, now=now, route=RouteInputs(...),
                                 run_started_at=run_started)
ranked = shortlist([(offer, assessment), ...])  # primary, uncertain, rejected
~~~

Route inputs bind item facts, freight, taxes and charges to the actual package and route. Conflicting mandatory charges leave the total unresolved. Overlapping charges count once. Verified, estimated and incomplete costs remain distinct, and only supported routes with complete verified totals qualify for primary ranking.

The original audit found no qualifying live international route. [Audit](international-source-audit.md) · [Detailed input schema, examples and ranking rules](https://github.com/han33-dotcom/TechScout/blob/9006e2ac8557003c958c31f729371ccb8e160b9a/docs/landed-costs.md)
