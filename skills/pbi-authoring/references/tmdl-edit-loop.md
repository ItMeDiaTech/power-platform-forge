# TMDL edit loop (semantic models)

`ppx pbi pull model "<model>" --workspace "<ws>"` writes the model's TMDL definition to
`<out>/<model>.SemanticModel/` (default `<out>` is the ppx exports folder) plus `.ppx-pull.json`,
ppx's record of what it pulled. Never edit or delete `.ppx-pull.json`.

## Folder layout (Learn, "Tabular Model Definition Language (TMDL)")

```text
<model>.SemanticModel/
  definition.pbism            settings of the model item
  .platform                   item type, display name, logical id
  definition/
    database.tmdl, model.tmdl, relationships.tmdl, expressions.tmdl (parameters and shared queries)
    tables/<Table>.tmdl       columns, measures, hierarchies and partitions of one table
    roles/, cultures/, perspectives/
```

## Small edits are plain text edits

Indentation is one tab per level. Object names with spaces, dots, `=`, `:` or `'` go in single quotes.

```tmdl
table Sales

	/// Revenue after discounts, in the sales currency
	measure 'Total Sales' = SUM(Sales[Net Amount])
		formatString: #,0.00
		displayFolder: Revenue

	measure 'Sales LY' =
			var ly = CALCULATE([Total Sales], SAMEPERIODLASTYEAR('Calendar'[Date]))
			return ly
		formatString: #,0.00
```

- Description: `///` lines directly above the object, no blank line between.
- Format string, display folder, hidden: properties one level below the object (`formatString:`,
  `displayFolder:`, `isHidden`).
- Multi-line DAX: the lines after `=` indented one level deeper than the properties.
- A new measure gets a `lineageTag` from the service on push; do not invent one.

Anything structural (new tables, relationships, calculation groups, roles, partitions) goes through
`/powerbi-authoring:semantic-model-authoring` on the pulled folder, which validates with the Tabular
Object Model instead of by eye.

## Push and check

```bash
ppx pbi push model "<model>" --workspace "<ws>" --from "<folder>" --dry-run   # which parts change
ppx pbi push model "<model>" --workspace "<ws>" --from "<folder>"
ppx pbi query --model "<model>" --workspace "<ws>" --dax 'EVALUATE ROW("v", [Total Sales])'
```

A measure change needs no refresh. A change to columns, partitions or data types may need the
model's data cleared: push exits 2 naming `--purge --yes`, after which the model is empty until
`ppx pbi refresh` (8 refreshes a day on shared capacity, scheduled ones included). Ask the user first.

Undo any push with the `undo` command in its envelope (`--restore <timestamp>`).
