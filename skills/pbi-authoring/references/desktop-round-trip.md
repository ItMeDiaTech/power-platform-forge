# Desktop round trip (branch D)

Used when the tenant lets this user read but not write definitions through Fabric (the probe
recorded no Fabric write source, or a push exited 2 naming `--desktop`). One step is attended: the
user saves in Power BI Desktop.

1. `ppx pbi pull model "<model>" --workspace "<ws>" --desktop` (or `pull report ... --desktop`)
   downloads the PBIX (Reports - Export Report In Group). A model is exported through a report bound
   to it, with the model included; a report whose model lives in another workspace comes back
   live-connected, so only the report can be edited.
2. `powerbi-desktop open "<file>.pbix"` (Desktop Bridge CLI) opens it in Desktop.
3. Model and measure edits: `/powerbi-authoring:semantic-model-authoring` connected to the running
   Desktop (local Modeling MCP). Report visuals: edited in Desktop by the user.
4. The user saves in Desktop (Ctrl+S).
5. `ppx pbi publish --pbix "<file>.pbix" --workspace "<ws>" --name-conflict CreateOrOverwrite`
   lists what it would replace (exit 2); after the user agrees, the same with `--yes`. ppx saves a
   PBIX backup of the current model first.

Things to know:

- The PBIX file name is the model and report name on publish. `pull model` names the file after the
  model, so publishing replaces that model; when the report it was exported through has another
  name, the pull warns that publishing also creates or replaces a report named like the file.
  `pull report` names the file after the report: when the model has another name, rename the file
  to the model's name before publishing, or publishing creates a new model of the report's name.
- Export limits (Learn, "Download a report from the Power BI service to Power BI Desktop"): needs
  Contributor or higher in the report's and the model's workspace; models with incremental refresh,
  the large semantic model storage format, Direct Lake or automatic aggregations cannot be
  downloaded through the API; reports created in the service on another workspace's model cannot be
  downloaded. ppx exits 2 with the report's URL for a manual download.
- Files over 1 GB cannot be published through the API.
