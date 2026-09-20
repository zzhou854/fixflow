# Model evaluation assets

This directory contains Git-versioned synthetic evaluation inputs and release-gate
policies. Runtime reports belong under `.artifacts/evaluations/` and must not be
committed.

The `resident_interpretation` corpus is engineering-authored synthetic Chinese
property-maintenance text. It contains no real resident data, does not represent
production traffic, and is not an independently audited blind test set. Its inputs
must not exactly copy the Prompt Library examples.

Changing case semantics, labels, suite, or severity requires a dataset version
change. Changing a threshold or critical/relative rule requires a policy version
change.
