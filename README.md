# CloudTally

Real-time cloud cost from the command line. Instead of billing exports
(BigQuery setup, 24-hour lag), CloudTally computes cost from **live resource
inventory priced against a rate catalog** — instant, forward-looking, and
queryable the moment a VM appears. GCP only for now; the provider interface
is designed so AWS and Azure can be added later.

```
inventory (Cloud Asset API | demo fleet)
        │
        ▼                    Cloud Billing Catalog API ─┐
  normalized Resources  ×  PricingCatalog (SKU keys) ◄──┤
        │                                bundled seed ──┘
        ▼
  SKU mapper (per asset type) ──► ResourceCost {components, confidence}
        │
        ▼
  Store (SQLite history) ──► run / watch / report / resources / history
```

## Install

```bash
pip install -e .          # demo mode needs nothing else
pip install -e ".[gcp]"   # live mode (google-auth, requests)
```

Requires Python 3.10+.

## Quick start (demo mode, no credentials)

```bash
cloudtally run
```

Demo mode simulates a realistic 3-project fleet (autoscaling web tier, spot
batch workers, an ephemeral CI runner, a stopped VM whose disk still bills)
priced with a bundled GCP list-price snapshot, with 7 days of backfilled
history on first run.

```bash
cloudtally report --by project          # or service, region, confidence
cloudtally resources --service "Cloud SQL" --format json
cloudtally history --hours 48
cloudtally resources --at "2026-07-05T14:00"   # time travel to a stored capture
```

## Live mode (real GCP)

```bash
# one-time setup
gcloud auth application-default login
# enable the Cloud Asset API on each project you want inventoried, and the
# Cloud Billing API on your ADC quota project:
gcloud services enable cloudasset.googleapis.com --project <each-project>
gcloud services enable cloudbilling.googleapis.com --project <adc-quota-project>

cloudtally run --mode live              # or set mode: live in config.yaml
cloudtally watch --mode live            # poll continuously, record history
```

Projects are auto-discovered: every ACTIVE project your credentials can see
(the `gcloud projects list` set), re-discovered hourly. Pin a subset with
`--project` or `gcp.projects` in config.yaml. A project that errors (API
disabled, no permission) is skipped and reported, never fatal.

## Commands

| Command | Purpose |
|---|---|
| `cloudtally run` | one inventory + pricing pass, record history |
| `cloudtally watch` | poll continuously (Ctrl+C to stop) |
| `cloudtally report --by service\|project\|region\|confidence` | aggregates from the latest capture |
| `cloudtally resources` | per-resource costs; filter with `--service`, `--confidence`, `-q`; time travel with `--at` |
| `cloudtally history --hours N` | cost time series |
| `cloudtally validate` | resolve and check the configuration |

`report`, `resources`, and `history` take `--format table|json|csv`;
`run` supports `--format json` and `--output-dir` to write `summary.json`
and `resources.json` for scripting.

## Configuration

Configuration lives in [`config.yaml`](config.yaml). Precedence:
CLI flags > `CLOUDTALLY_*` env vars > config.yaml > defaults. A different
file can be selected with `-c` or `CLOUDTALLY_CONFIG=/path/to/config.yaml`.

| Env var | `config.yaml` key | Default | Meaning |
|---|---|---|---|
| `CLOUDTALLY_MODE` | `mode` | `demo` | `demo` or `live` |
| `CLOUDTALLY_GCP_PROJECTS` | `gcp.projects` | — | project IDs to inventory (live) |
| `CLOUDTALLY_GCP_API_KEY` | `gcp.api_key` | — | API key for the Billing Catalog API (else ADC) |
| `CLOUDTALLY_CURRENCY` | `currency` | `USD` | USD, EUR, GBP, AUD, CAD, SGD, JPY, INR, VND |
| `CLOUDTALLY_POLL_SECONDS` | `poll_seconds` | 15 demo / 300 live | `watch` poll interval |
| `CLOUDTALLY_CATALOG_REFRESH_SECONDS` | `catalog_refresh_seconds` | 86400 | pricing catalog refresh |
| `CLOUDTALLY_INCLUDE_UNPRICED` | `gcp.include_unpriced` | off | inventory all asset types, not just priced ones |
| `CLOUDTALLY_DB` | `db_path` | `data/history-<mode>.db` | SQLite history path |

Live prices are converted server-side by the Billing Catalog API; the
bundled seed uses approximate static FX rates. History snapshots keep the
currency they were recorded in, so a time series mixes units across a
currency change.

## Pricing coverage and confidence

Every resource carries a confidence level, so an unpriceable resource never
silently reads as "$0 and fine":

- **exact**: fully rate-based. VM vCPU/RAM (per family, including Spot and
  custom/shared-core shapes), persistent disks, static IPs, GKE cluster
  fees, Cloud SQL vCPU/RAM/storage (zonal and HA), Memorystore Redis.
- **estimated**: priced with stated assumptions (bucket capacity without
  ops/egress, forwarding rules without data processing, Cloud NAT gateways
  without data processing, VMs with premium OS licenses excluded).
- **usage_based**: Cloud Run, Cloud Functions, egress, requests. Real cost
  depends on usage that resource inventory cannot see, so these are flagged
  rather than guessed.
- **unpriced**: asset types with no mapper yet; listed, never guessed.

This is the fundamental trade-off of the inventory-times-pricing approach:
it is instant and forward-looking for the provisioned share of spend
(typically 60-80%), and it cannot replace billing data for the usage-based
share.

## Layout

```
cloudtally/
  cli.py                  argparse CLI: run / watch / report / resources / history / validate
  engine.py               inventory → pricing poll loop, demo history backfill
  store.py                current fleet state + SQLite cost history
  models.py               Resource / CostComponent / ResourceCost
  config.py               CLI/env/yaml configuration
  pricing/
    catalog.py            SKU index, unit-to-hourly normalization
    seed_gcp.py           bundled GCP list-price snapshot
    gcp_live.py           Cloud Billing Catalog API fetch + SKU normalizer
    fx.py                 static FX rates for the seed catalog
  providers/
    base.py               CloudProvider interface (implement for AWS/Azure)
    gcp/
      provider.py         wires inventory + catalog + mapper, demo/live
      live_inventory.py   Cloud Asset Inventory API → Resources
      demo_fleet.py       deterministic synthetic fleet
      mapper.py           asset type → cost components (the SKU mapping)
      machine_types.py    machine type name parsing
```

## Adding AWS/Azure

Implement `CloudProvider` ([`cloudtally/providers/base.py`](cloudtally/providers/base.py)):
an inventory source (AWS Config / Resource Explorer, Azure Resource Graph),
a pricing source (AWS Price List API, Azure Retail Prices API) normalized to
catalog keys, and a mapper. The engine, store, and CLI are provider-agnostic.

## Caveats

- Prices are list prices unless you wire up the newer GCP Pricing API
  (account-specific negotiated rates); CUDs/SUDs are not modeled yet.
- Bucket sizes are not in Cloud Asset Inventory, so live mode flags buckets
  usage-based unless you add a Cloud Monitoring lookup.
- Multi-region bucket locations (`US`, `EU`) don't map to a regional SKU and
  fall back to unpriced.
- Tiered SKUs use the first non-zero tier.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Run the tests with:

```bash
pip install -e ".[dev]"
pytest
```

## License

[MIT](LICENSE)
