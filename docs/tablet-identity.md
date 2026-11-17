# Tablet identity

Task [#43](https://github.com/han33-dotcom/TechScout/issues/43) adds a tablet profile to the
v2 `compare_offers`, `variant_identity_key` and `group_variants` functions in
`app/identity.py`. It changes no wire contract and leaves phone and laptop keys unchanged; a
test pins both. Monitor and audio identity are described in
[monitor-audio-identity.md](monitor-audio-identity.md).

## Fields compared

- **Fields:** manufacturer, model, color, `storage_gb` and `connectivity` (`wifi` or
  `cellular`). The tablet entry sits in the shared `_PROFILE_FIELDS` table.
- **Generation, chip and screen size:** `TabletSpecs` has no field for these, so they come
  only from the evidenced `variant.product.model`, as laptop generation does.
- **Stylus support:** a capability of the model, not a configuration a buyer picks, so it is
  left to eligibility.
- **Activation:** cellular tablets get no activation term unless one is observed.

## How a model name is read

The name rule, `_tablet_model`, runs in `_identity_facts` beside the monitor and audio rule.
It works on the evidenced name after the shared normalization (case, typographic dashes,
spaces, and `S9+` spelled `S9 Plus`).

### 1. One spelling

Retail spellings are unified so that the same device compares equal:

| Kind | Spellings | Becomes |
|---|---|---|
| Screen size | `11"`, `11''`, `11 in`, `11 inch`, `11″`, curly quotes, a decimal comma (`12,9"`) | `11-inch`, `12.9-inch` |
| Generation | `10th gen`, `10th-gen`, `Gen 10` | `10th generation` |
| Line | `iPad-Pro`, `iPadPro`, invisible web characters (zero-width spaces, soft hyphens) | `ipad pro` |
| Chip | `with M2 chip`, `M2 chip` | `m2` |
| Storage and Wi-Fi | `128 GB`, `WiFi` | `128gb`, `wi-fi` |
| Plus | a spaced `+` at the end, or before storage or connection words (`Galaxy Tab S9 +`) | `plus` |

Only 7 to 19.99 inches is read as a screen size, so `2-in-1` is not a size.

Parentheses, commas and a leading maker that repeats the evidenced manufacturer drop out.

### 2. Checked against the offer's own facts

Storage and Wi-Fi/cellular words in the name are checked against the offer's own
`storage_gb` and `connectivity` facts. A name that contradicts them is not trusted, for
example `... Wi-Fi + Cellular` on a Wi-Fi offer or `... 256GB` on a 128 GB offer.

- Memory is not storage: `8GB RAM` and the first number of `8/128` are skipped.
- French `Go` and `To` are storage units.
- The words stay in the compared name. They can keep two names apart but never merge them:
  an LTE and a 5G model, or `4/128` and `6/128`, stay different.

### 3. A closed vocabulary

Every word of the name must be one the rule recognises:
- line words and suffixes (`ipad`, `air`, `pro`, `mini`, `galaxy`, `tab`, `fe`, `plus`,
  `lite`, `ultra`, `max`, `surface`, `fire`, `hd`, `legion`, `matepad`, `mediapad`, `pad`, ...);
- sizes, storage, memory pairs, `ram`, connection words and color words;
- bare one- or two-digit numbers;
- the edition evidence listed below.

**Any other word leaves the name unresolved.** A retail title is often more than one model's
name, and an unknown word is where the extra meaning hides:

| Title | Problem word |
|---|---|
| `Supports Apple Pencil (2nd Generation)` | an accessory's generation |
| `2000 x 1200` | a display resolution |
| `Intel Core i7` | a CPU |
| `A7 chip, M7 coprocessor` | a coprocessor |
| `7th/8th Gen`, `2019/2020`, `M1/M2` | alternatives |
| `Galaxy Tab S9 + Book Cover` | a bundle |
| `iPad Pro11`, `iPad Air6`, `Fire HD10` | a line word joined to a number |
| `i-Pad`, `iPad™`, `iPad.Pro` | an unknown spelling |

Extraction is expected to supply the model name itself, not the whole listing title.

### 4. Positive evidence of one edition

A name identifies one model only with positive evidence of its edition:
- an ordinal generation (`10th generation`);
- a year from 2010 to 2039 (`2024`);
- an Apple chip (`M2`, `A17 Pro`, `A12Z`);
- a model token that joins letters and digits (`S9`, `P11`, `Y700`, `SM-X710`).

A bare number never counts. Retail titles carry sizes, warranty years, operating-system
versions and accessory numbers that name no edition (`iPad 10.2`, `Surface Pro 9`, `Windows
11`, `Apple Pencil 2`). An Intel CPU tier (`i5`, and `m3` in a Surface name) is allowed but is
not evidence either.

### 5. Still unresolved, even with evidence

- **Shared chips and years.** A chip or year shared by two generations of one line does not
  count: the base iPad's `A10` (in any spelling: `A10 Fusion`, `A10 Bionic`) and `2012`, and
  the iPad mini's `A7`.
- **Two editions.** The name carries two generations, two years or two chips.
- **Apple without iPad.** An Apple name without the word `iPad`.
- **Sizes.**
  - The name carries two screen sizes.
  - A line sold in more than one size under one name gives no size: iPad Air and iPad Pro,
    Galaxy Tab S2, S, A and E and numbered Tabs, and the MediaPad, MatePad and Yoga lines.
- **Dated lines.** A line sold under one name across several editions gives no year or
  generation: Galaxy Tab S6 Lite and A7, Lenovo Tab M7, M8, M10 and P11, Fire, Redmi Pad and
  Legion Y700. Words after the line name (`Tab M10 Plus 64GB`) do not hide it.

Two identical unresolved names never match. An unresolved name against a specific one is
unresolved, not different.

Numbers, chips and suffixes stay exact: M2 is not M3, S9 is not S9+ or S9 FE, and 12.9-inch
is not 13-inch.

## Limitations

- **Spelling differences still compare as different.** Model text is otherwise exact, so the
  same device can look like two devices, which fails safe:
  - storage, connection or color words present in one name only (`iPad Air 11" M2 Wi-Fi
    128GB` versus `iPad Air 11-inch (M2)`);
  - token order (`iPad Air M2 11-inch` versus `iPad Air 11-inch (M2)`);
  - chip versus ordinal (`iPad (A16)` versus `iPad 11th generation`);
  - joined suffixes (`Galaxy Tab S9FE` versus `Galaxy Tab S9 FE`).
- **Some real single-model names stay unresolved**, which also fails safe:
  - names with no edition evidence, including nicknames (`Pixel Tablet`, `Xiaomi Pad 6`,
    `Surface Pro 9`, `OnePlus Pad 2`, `iPad mini 7`);
  - names with a word outside the vocabulary (a regional note such as `Middle East Version`,
    or an unlisted line or color).
- **The lists are owned, not a catalog.** The multi-size and multi-edition lists are short.
  Another reused line name that carries a model token can still match two editions.
- **Missing spec fields.** `TabletSpecs` has no SIM, region, memory, CPU or glass field. So
  regional cellular SKUs, memory or CPU differences named nowhere in the model text, and glass
  options cannot be told apart.
- **Eligibility is separate.** v2 eligibility's exact-model check still uses the shared model
  normalization, not this tablet rule.
- **Synthetic tests only.** All fixtures are synthetic, and no live retailer calls were made.

## Tests

`tests/test_identity.py` covers the tablet profile:
- the recipe cases in `tests/fixtures/identity/full-product.json`;
- unknown, contradicting and missing facts;
- ambiguous names and real retail titles that fit several tablets;
- cross-retailer spelling pairs in both directions;
- memory versus storage;
- category mixing.

A deliberate-break check removes each rule in turn and confirms that a test fails.
