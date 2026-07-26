# Artifact contracts

Executable validation and fixture artifacts are versioned with
`schema_version`, `artifact_version`, and `producer_version`. Validation
commands use argument arrays, closed stdin, explicit timeouts, expected exit
codes, environment allowlists, bounded output, and repository-contained working
directories. Legacy descriptive artifacts are rejected as `unsupported` rather
than guessed into executable commands.

Gap records carry their origin and `resolution_source`. Semantic observations
are advisory until repository evidence confirms them; unresolved policy stays
unresolved.
