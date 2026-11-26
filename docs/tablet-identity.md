# Tablet identity

The [identity module](../app/identity.py) compares manufacturer, evidenced model name, color, storage and connectivity. The model name also carries generation, chip and screen size. Stylus support belongs to eligibility. A cellular model needs evidence for any activation terms.

Normalize retail spellings such as `11″` and `11-inch`, `10th gen` and `10th generation`, or `S9+` and `S9 Plus`. Preserve distinctions such as M2 versus M3, Wi-Fi versus cellular, or S9 versus S9 FE. Names that allow several editions stay unresolved, even when the same ambiguous name appears at both shops.

Exact grouping can miss legitimate matches when a part number is absent or the seller reorders tokens. Region, RAM and CPU differences are distinguishable only when available to the profile. Use the [normalization reference](https://github.com/han33-dotcom/TechScout/blob/9006e2ac8557003c958c31f729371ccb8e160b9a/docs/tablet-identity.md) for the supported vocabulary, conflicting title/spec cases and known exceptions.

[Identity tests](../tests/test_identity.py) · [Synthetic fixtures](../tests/fixtures/identity/full-product.json) · [Monitor and audio identity](monitor-audio-identity.md)
