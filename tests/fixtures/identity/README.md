# Identity fixtures (MVP 07)

`white_url_black_product.json` is a fictional, synthetic offer. It stands in for the
White-URL/Black-product case captured during source selection, which is not in this
repository: none of the minimized store captures in `tests/fixtures/stores` contains a
listing whose link names one color while the selected variant's own facts name another.
The fixture reproduces that shape (a link naming White, a selected variant whose facts say
Black) with invented IDs and URLs. It is not real retailer data.

The captured shared-SKU and parent-option cases are read from `tests/fixtures/stores`:
`tests/test_identity.py` (`CapturedStoreTests`) reads each store twice and checks that
`deduplicate` keeps the variants apart and merges the re-reads, and
`tests/test_end_to_end.py` runs the same captures through the whole run.
