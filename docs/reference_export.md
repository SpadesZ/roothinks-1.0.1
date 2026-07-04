# Reference Export

產生時間: 2026-07-04 19:24 +08:00

`app/services/metadata_service.py` supports local metadata normalization and export.

## Formats

- BibTeX
- RIS
- CSL JSON

## Fields

Supported fields include `title`, `authors`, `year`, `journal`, `doi`, `url`, `abstract`, and `publisher`.

Missing fields are skipped. DOI is never fabricated.

## Citation Key

BibTeX keys use `first_author_lastname + year + short_title`, sanitized for BibTeX compatibility.

## CLI

```bash
python scripts/export_references.py --project-id PROJECT_ID --format bibtex
python scripts/export_references.py --project-id PROJECT_ID --format ris
python scripts/export_references.py --project-id PROJECT_ID --format csl-json
```

## Future DOI Lookup

`MetadataLookupProvider` defines the interface for future CrossRef/OpenAlex adapters. External lookup must remain optional, timeout-bound, and mock-tested.
