# Brazil Elections & Polling Data (TSE) (`apify-brazil-elections`)

Extract structured Brazilian election data directly from official **TSE (Tribunal Superior Eleitoral)** static CDN JSON & open-data files with zero residential proxies:

- **`results`:** Consolidated candidate vote counts, vote percentages, coalition/party, vice candidates, and election status (`Eleito` / `Não eleito`) for Brazil (`br`), states (`sp`, `rj`, `mg`, `al`, ...), or individual municipalities (`municipalityCode`).
- **`elections_catalog`:** Live directory of available cycles (`ele2026`, etc.), pleitos, rounds, scopes, and offices from `ele-c.json`.
- **`municipalities`:** Full TSE municipal codes (`5` digits), IBGE codes (`7` digits), and electoral zones (`mun-e*-cm.json`).
- **`polling_stations`:** Geocoded polling locations (school/venue name, address, neighborhood, latitude/longitude, section count) filtered by state or query.

## Example Input

```json
{
  "mode": "results",
  "cycle": "ele2026",
  "electionId": 6258,
  "scope": "br",
  "officeCode": 1,
  "maxResults": 20
}
```

## MCP & Standby Ready

Runs as an Apify batch Actor, Standby HTTP endpoint, or MCP Server (`query_brazil_elections`).
