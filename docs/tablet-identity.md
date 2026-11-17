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
It reads the evidenced name after the shared normalization (case, typographic dashes, spaces,
and `S9+` spelled `S9 Plus`).

**A name that does not clearly name one model stays unresolved.** Two identical unresolved
names never match, and an unresolved name against a specific one is unresolved, not different.
Numbers, chips and suffixes stay exact: M2 is not M3, S9 is not S9+ or S9 FE, and 12.9-inch is
not 13-inch.

### 1. One spelling

Retail spellings are unified so that the same device compares equal:

| Kind | Spellings | Becomes |
|---|---|---|
| Screen size | `11"`, `11''`, `11 in`, `11 inch`, `11″`, curly quotes, a decimal comma (`12,9"`), trailing zeros (`11.0"`) | `11-inch`, `12.9-inch` |
| Generation | `10th gen`, `10th-gen`, `Gen 10` | `10th generation` |
| iPad line | `iPad-Pro`, `iPadPro`, invisible web characters (zero-width spaces, soft hyphens) | `ipad pro` |
| Chip | `with M2 chip`, `M2 chip`; `A17 Pro` | `m2`; `a17-pro` |
| Color phrase | `Space Gray`, `Light Gold` | `space-gray`, `light-gold` |
| Storage and Wi-Fi | `128 GB`, `WiFi` | `128gb`, `wi-fi` |
| Display suffix | `M10HD`, `M8FHD` | `m10 hd`, `m8 fhd` |
| Plus | a spaced `+` at the end, or before storage or connection words (`Galaxy Tab S9 +`) | `plus` |

Also:
- Only 7 to 19.99 inches is read as a screen size, so `2-in-1` is not a size.
- A dash with spaces on both sides separates parts of a title (`... (M2) - Space Gray`).
- Parentheses and commas drop out.
- A leading maker drops out, word by word: `Apple` or `Apple Inc.` for the evidenced `Apple
  Inc.`. A maker word that is itself a tablet word stays (`Surface` for `Microsoft Surface`, or a
  maker evidenced as `iPad`).

### 2. Checked against the offer's own facts

Storage and Wi-Fi/cellular words in the name are checked against the offer's own
`storage_gb` and `connectivity` facts. A name that contradicts them is not trusted, for
example `... Wi-Fi + Cellular` on a Wi-Fi offer or `... 256GB` on a 128 GB offer.

- Memory is not storage: `8GB RAM` and the first number of `8/128` are skipped.
- French `Go` and `To` are storage units.
- The words stay in the compared name. They can keep two names apart but never merge them:
  an LTE and a 5G model, or `4/128` and `6/128`, stay different.

### 3. A closed vocabulary

Every word must be one the rule recognises:
- line words and suffixes (`ipad`, `air`, `pro`, `mini`, `galaxy`, `tab`, `fe`, `plus`,
  `lite`, `ultra`, `max`, `surface`, `fire`, `hd`, `fhd`, `legion`, `matepad`, `mediapad`,
  `pad`, ...);
- sizes, storage, memory pairs, `ram`, `2-in-1` and connection words;
- whole color names;
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
| `i-Pad`, `iPad™`, `iPad.Pro`, `Tab-A`, `Galaxy Tablet` | an unknown spelling |
| `S6 Light` | a fragment of a color (`light gold`) |

Extraction is expected to supply the model name itself, not the whole listing title.

### 4. Positive evidence of one edition

A name identifies one model only with positive evidence of its edition:
- an ordinal generation written with its correct suffix (`10th generation`; `3th` and `03rd`
  leave the name unresolved);
- a year from 2010 to 2039 (`2024`);
- an Apple chip (`M2`, `A17 Pro`, `A12Z`);
- one short model token that joins letters and digits (`S9`, `P11`, `Y700`);
- one manufacturer part number (`SM-X710`, `TB125FU`).

A name with two generations, years, chips, model tokens or part numbers (`Galaxy Tab S8, S8+`)
stays unresolved.

Some things never count as evidence:
- a bare number: retail titles carry sizes, warranty years, operating-system versions and
  accessory numbers that name no edition (`iPad 10.2`, `Surface Pro 9`, `Windows 11`,
  `Apple Pencil 2`);
- an Intel CPU tier (`i5`, and `m3` in a Surface name);
- a year in a Surface name, because Surface Pro years name more than one model (2013, 2021,
  2024 and 2025).

### 5. iPad rules

A name is read as an iPad when it contains `iPad` or the evidenced maker starts with `Apple`.
It then stays unresolved when:
- `iPad` is missing (`Air M2`) or appears twice (`iPad - iPad Pro ...`);
- a line word (`Air`, `Pro`, `mini`) is not right after `iPad` (`iPad M4 Pro`, `iPad 128GB Air`);
- a base iPad name carries a chip or year that no base iPad had (`iPad 11-inch (M2)`,
  `iPad 2024`): the base line had the A4, A5, A5X, A6X, A9, A10, A12, A13, A14 and A16, and
  the years 2010–2012, 2017–2022 and 2025;
- an iPad Air or Pro gives no size (`iPad Air (M2)`, `iPad Pro 2024`);
- its only evidence is shared by two editions:
  - the base iPad's `A10` (6th and 7th generation) and `2012` (3rd and 4th);
  - the iPad mini's `A7` (mini 2 and 3);
  - the iPad Pro 11-inch's 3rd to 6th generation. Apple counts that line per size, so the
    2021 M1 model is its 3rd generation. Many retailers count across the whole Pro family,
    which makes the 2018 model the 3rd generation.

### 6. Other lines

A non-Apple name stays unresolved when:
- **Radio missing.** The offer is cellular but the name does not say `LTE`, `3G`, `4G` or `5G`,
  because some lines ship both (Galaxy Tab S7 FE LTE and 5G).
- **Year only.** Its only evidence is a year and it gives no size (`Honor Pad 5 (2019)`,
  `Redmi Pad SE (2024)`).
- **Size missing.** It names a line sold in more than one size under one name, without the
  size: Galaxy Tab S2, `Tab S`, `Tab A`, `Tab E` and numbered Tabs, `Pad 5`-style numbered
  Pads, MediaPad, MatePad, Yoga and Surface Pro.
- **Edition missing.** It names a line sold under one name across several editions, without
  a year or generation: Galaxy Tab S6 Lite, S2 and A7, Lenovo Tab M7, M8, M10 and P11, Fire,
  Redmi Pad and Legion Y700. Words after the line name (`Tab M10 Plus 64GB`) do not hide it.
- **Part number missing.** It is a Lenovo Tab M10 Plus (also `M10 FHD Plus`) without a part
  number, because the 3rd Gen ships on two chips under one name (TB125FU and TB128FU).
- **Refresh.** It is a Lenovo Tab M8 (4th Gen) without a year or part number, because a 2024
  refresh kept the name (TB300FU and TB301FU).
- **HD or FHD missing.** It is an early Lenovo Tab M8 or M10 (a 2019 or 2020 year, or the 1st or
  2nd generation) without `HD` or `FHD`, because those came in both.

## Limitations

- **The lists are owned, not a catalog.** The multi-size, multi-edition, two-chip, refresh
  and HD lists hold the cases found so far. Another line sold under one name in two sizes, editions,
  radios or chips can still match until it is added, unless the name carries a part number.
  Extraction should supply the manufacturer's part number when a listing shows one.
- **Spelling differences still compare as different.** Model text is otherwise exact, so the
  same device can look like two devices, which fails safe:
  - storage, connection or color words present in one name only (`iPad Air 11" M2 Wi-Fi
    128GB` versus `iPad Air 11-inch (M2)`);
  - token order (`iPad Air 11-inch M2` versus `iPad Air M2 11-inch`);
  - chip versus ordinal (`iPad (A16)` versus `iPad 11th generation`);
  - joined suffixes (`Galaxy Tab S9FE` versus `Galaxy Tab S9 FE`);
  - other color spellings (`space-gray` versus `space-grey`).
- **Some real single-model names stay unresolved**, which also fails safe:
  - names with no edition evidence, including nicknames (`Pixel Tablet`, `Xiaomi Pad 6`,
    `Surface Pro 9`, `OnePlus Pad 2`, `iPad mini 7`);
  - names with a word outside the vocabulary (a regional note such as `Middle East Version`,
    or an unlisted line or color);
  - names the rules above treat with caution (`Fire HD 10 (2021)`, `Galaxy Tab A 8.0 (2019)`,
    a cellular Android name without its radio).
- **Missing spec fields.** `TabletSpecs` has no SIM, region, memory, CPU or glass field. So
  regional cellular SKUs, and memory or CPU differences named nowhere in the model text,
  cannot be told apart.
- **Eligibility is separate.** v2 eligibility's exact-model check still uses the shared model
  normalization, not this tablet rule.
- **Synthetic tests only.** All fixtures are synthetic, and no live retailer calls were made.

## Tests

`tests/test_identity.py` covers the tablet profile:
- the recipe cases in `tests/fixtures/identity/full-product.json`;
- unknown, contradicting and missing facts;
- ambiguous names and real retail titles that fit several tablets;
- positive pairs that must still match;
- cross-retailer spelling pairs in both directions;
- memory versus storage;
- category mixing.

A deliberate-break check removes each rule in turn and confirms that a test fails.
