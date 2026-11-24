# LobbyGraph pipeline (proof of concept)

Builds the LobbyGraph knowledge graph for the groups in `config/groups.yaml`
(PoC: Siemens and Siemens Energy) and loads it into Neo4j.

```
raw sources ──► sources.py ──► transform.py ──► checks.py ──► export.py ──► Neo4j
 (register, GLEIF,             (nodes + edges,   (DataIssue    (CSV, Cypher
  Commission meetings)          with provenance)  nodes)        load, summary)
```

## What each step does

| Step | File | Notes |
|---|---|---|
| Read sources | `src/lobbygraph/sources.py` | Transparency Register export; GLEIF (online API or offline cache); Commission meetings |
| Build graph | `src/lobbygraph/transform.py` | Group membership is derived from GLEIF (ultimate accounting parent = group parent). Memberships and consultancies are resolved from free text to register entries, with the match method and confidence stored on every link |
| Check data | `src/lobbygraph/checks.py` | Possible double counting, different reporting periods, head office mismatches, unresolved names, unknown stake sizes, partial ownership data |
| Export / load | `src/lobbygraph/export.py` | CSV files per node type, `edges.csv`, `data_issues.csv`, `summary.json`; `load_neo4j()` rebuilds the graph with batched `UNWIND` |
| Answer questions | `queries/poc_questions.cypher` | Tested read-only query templates for PoC questions 1, 2, 5, 8 and 11 |
| Local check | `src/lobbygraph/answers.py` | The same five questions answered in Python, to test the output without Neo4j |
| Picture | `src/lobbygraph/draw.py` | Overview image of the core graph |

## Run it

```bash
pip install -r requirements.txt
# The register download is .xls; convert once:  soffice --headless --convert-to xlsx ODP28-10-2026.xls
PYTHONPATH=src python -m lobbygraph.cli --register data/raw/transparency_register/ODP28-10-2026.xlsx
# fetch every subsidiary from GLEIF and load Neo4j (in Azure: a Container Apps Job)
PYTHONPATH=src python -m lobbygraph.cli --register ... --online --neo4j
```

`--neo4j` reads `NEO4J_URI`, `NEO4J_USER` and `NEO4J_PASSWORD` from the environment
(in Azure these come from Key Vault through the job's managed identity).

## Status of the PoC run (2 November 2026)

* **Transparency Register:** full export of 28 October 2026 (18,068 entries); all three Siemens registrations read.
* **GLEIF:** offline cache with 10 checked entities. Online mode fetches all subsidiaries
  (572 for Siemens AG, 105 for Siemens Energy AG on 30 October 2026); run it in Azure, where the API is reachable.
* **Commission meetings:** the Commission's published lists (downloaded 2 November 2026): cabinet meetings
  2024-2029 and meetings of Directors-General and management staff. Put them in `data/raw/meetings/` as
  `cabinet_meetings_2024_2029.xlsx` and `dg_meetings.xlsx`. Meetings are linked to registrations by
  Transparency Register ID (no name matching); rows listing several officials are grouped back into one meeting.
* **Neo4j load:** code written but not yet run against a database; first run happens in the Azure environment.

## Run it in Azure (loader job)

**PoC mode (source files built into the image).** Used when a policy blocks public network access to storage.

1. In Azure Cloud Shell, unzip this package and copy the three source files into it:
   * `data/raw/transparency_register/` the register export (.xls)
   * `data/raw/meetings/` both Commission meeting files, with their original names
2. Build the image: `az acr build --registry crlobbygraphdev --image lobbygraph-loader:0.1 .`
3. Create a Container Apps Job (manual trigger) from that image, with environment variables `NEO4J_URI`
   (`bolt://ca-neo4j-lobbygraph-dev:7687`), `NEO4J_USER` and `NEO4J_PASSWORD` (secret). Leave `STORAGE_ACCOUNT` unset.
4. Each run fetches all subsidiaries from GLEIF and rebuilds the graph in Neo4j. To refresh the data, put new
   files in the folders and rebuild the image with a new tag.

**Storage mode (later).** Set `STORAGE_ACCOUNT` and `AZURE_CLIENT_ID`; the job then downloads the newest files from the
`raw` container and writes its outputs to `dumps/runs/<timestamp>/`. Needs network access from the Container Apps
environment to the storage account (for example a private endpoint).
