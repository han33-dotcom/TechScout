# Why 35 runs abstained · November 24, 2026

At revision `7254524`, only eight of 35 recorded runs reached offer verification. Sixteen sent no store request, six read pages without extracting a usable product, and five could not check the attempted pages or leads. Better reasoning at the final verification step alone could not have recovered those 27 runs.

For the identified sellers that reached verification, condition, item-specific delivery or mandatory charges remained unknown. Extraction also missed purchasability evidence and WooCommerce variant fields. A synthetic page containing every required fact yielded a USD 902 payable total. That test isolated a working path through the rules. The live runs produced zero verified offers.

The controller at the time also required domestic dispatch. The current [buyer contract](buyer-product-contract.md) instead checks whether the actual seller is Lebanese and serves the buyer’s destination.

[Original per-run findings, source reads and proposed repairs](https://github.com/han33-dotcom/TechScout/blob/9006e2ac8557003c958c31f729371ccb8e160b9a/docs/abstention-diagnosis-2026-11-24.md) · [Subsequent results](buyer-architecture-comparison.md)

## Reproduce the fixture check

```sh
python -m unittest discover -s tests -p test_domestic_decisive_facts.py -v
```
