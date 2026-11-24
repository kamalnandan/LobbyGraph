# LobbyGraph

See the whole company behind EU lobbying, with a source for every link.

LobbyGraph is a knowledge graph of corporate groups, their EU lobby registrations
and their meetings with the European Commission. It starts with the energy sector
(10 groups: EDF, TotalEnergies, Siemens Energy, ENGIE, Eni, Enel, Iberdrola, RWE,
ORLEN, E.ON).

## Folders

| Folder | What it is |
|---|---|
| `lobbygraph/` | Data pipeline (loader): reads the sources, builds the graph, runs data checks, loads Neo4j |
| `app/` | Streamlit web app with email sign-in, served over HTTPS by Caddy |
| `docker-compose.yml` | Runs neo4j, app and caddy; the loader runs on demand |

## Data sources

- EU Transparency Register (lobby registrations)
- GLEIF LEI data (company ownership: who is part of which group)
- European Commission meetings with organisations (cabinets and directors-general)

## Run on the VM

```bash
cp .env.example .env        # fill in the values
docker compose up -d        # neo4j, app, caddy
docker compose build loader && docker compose run --rm loader   # load the graph
docker compose restart app  # clear the app cache after a load
```

Which groups are loaded is set in `lobbygraph/config/groups.yaml`.
