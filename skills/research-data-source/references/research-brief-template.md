# Research brief: <dataset> from <provider>

*Date: <YYYY-MM-DD>. Mark anything not confirmed by a real request as **unverified**.*

## Need

<the spec from step 1: variable, unit, region, resolution, period, time step,
constraints>

## Recommendation

- **Source:** <provider, dataset name, version>, <landing page URL>
- **Why:** <one or two sentences>
- **Recommended integration path:** <A | B | C | D | E> (see
  `skills/add-data-source/references/integration-paths.md`), because <reason>
- **Proposed `source` name:** `<identifier>`

### Alternatives considered

| Candidate | Why not |
|-----------|---------|
| | |

## Access

- **Protocol / API:** <WCS 2.0.1 / OGC API / REST / file server / CDS / STAC>
- **Endpoint:** `<base URL>`
- **API documentation:** <URL>
- **Auth:** <none / API key in ~/.xyz / env var NAME>; how a user obtains it
- **Rate limits / terms of use:** <...>
- **Subsetting:** bbox <yes/no>, time <yes/no>, variables <yes/no>
- **Python client needed:** <none (requests) / package + version>

### Verified sample request

```text
<exact URL, or the client call with its parameters>
```

- Status / content type / size: <200, image/tiff, 12 kB>
- Request for the full extent datavia needs: <URL pattern, number of
  requests, approximate volume per request; no timings>

## Data characteristics (observed)

| Property | Value |
|----------|-------|
| Format | |
| CRS | |
| Resolution / grid | |
| Pixel registration | cell centre / corner (how verified) |
| Extent | |
| Dimensions and order | |
| Variable names (native) | |
| Units, scale factor / offset | |
| Nodata / fill values | value, and whether it is declared in metadata |
| dtype | |
| Time coordinate | UTC?, interval start / end, accumulation window |
| Temporal coverage / update cadence | |
| Sanity value | <place, coordinates>: <value + unit>, plausible? |

## Licence and attribution

- **Licence:** <name, URL>
- **Required attribution / citation (verbatim):**

  ```text
  <exact text from the provider>
  ```

## Gotchas

- <e.g. "-9999 used as fill but not declared as nodata">
- <e.g. "precipitation stamped at interval end">

## Open questions

- <anything that needs a decision by the user or couldn't be verified>

## Probe script

<kept at /full/path/probe.py | deleted after the run>
