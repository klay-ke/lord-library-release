# PG&E monthly collection

Runs on the 15th at 00:00 America/Los_Angeles (DST aware). GitHub may delay scheduled jobs. The iOS client separately reads existing UtilityAPI data from 06:00; reads and PDF downloads do not initiate collection.

Repository secrets:
- `UTILITYAPI_TOKEN`: UtilityAPI API token.
- `UTILITYAPI_METER_UID`: the single authorized PG&E meter to collect.

No credentials, meter IDs, statements, or usage data are committed or uploaded as Action artifacts. Manual dispatch defaults to a read-only check. No automatic HTTP retries, paid monitoring, additional meters, or plan changes.

The script skips when collection is pending or collection notes show data collected this LA calendar month. Before POST it atomically creates a `utilityapi-collection-YYYY-MM` tag as a persistent attempt marker. If the request times out, fails, or is rejected, reruns cannot trigger another potentially chargeable collection. Inspect the UtilityAPI dashboard before manually removing such a marker. A 402 requires account-side resolution; the workflow never purchases credits. UtilityAPI's monthly free collection is account-wide, so other manual collections can consume it.

The current request covers 12 months for one meter. Successful first monthly collections are eligible for UtilityAPI's free allowance; the script does not represent API notes as an authoritative billing ledger. Do not manually collect other meters on this account while relying on the allowance.

Test: `python -m unittest discover -s automation/utilityapi`

Public repositories' schedules may be disabled by GitHub after 60 days without repository activity. Check Actions if scheduled runs stop.
