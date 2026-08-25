# Validation contract

Authoritative read-only lexical rule `accepted-prefix-v2`:

For textual input, the accepted representation begins with the exact
case-sensitive prefix `GOOD-`. The entire non-empty suffix immediately after
that prefix consists exclusively of ASCII decimal characters in the inclusive
range `0` through `9`. The prefix is an external data contract, not a display
label or compatibility alias. Unicode digit classes are outside this lexical
rule, so `str.isdigit()`, `str.isdecimal()`, and regular-expression `\d` are
not valid implementations of the suffix check.

The public API binding, non-text input behavior, and executable boundary
examples are intentionally owned by:

`binding_test=tests/test_validator.py`

Read that binding test next, then follow its import to the implementation. Do
not infer those facts from this lexical contract, and do not edit the contract
or tests. Repair only the imported implementation without weakening suffix
validation or adding an example-specific special case. A focused offline
`unittest` run may guide the repair, but final full test discovery is still
required.
