"""Golden-baseline harness for the immutable-version-model migration (Task G).

Captures the CURRENT outputs of the read paths (dependency graph, dashboard
alignment, asset diff) as normalized, stable-keyed JSON fixtures so the M2
manifest-based read-path ports can be proven *equivalent* to today's behavior.

The fixtures key everything on stable identifiers (asset ``key`` lineage strings
+ semver, curriculum ``slug``), never on volatile UUIDs or wall-clock
timestamps, so they survive a re-seed unchanged.
"""
