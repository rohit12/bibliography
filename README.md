# Bibliography

A cross-book bibliography kept as plain JSON in git. Each paper is stored once; every endnote that cites it, in every book, is a separate row. The public page and the data files are generated from that; personal notes stay private.

Started from the 403 references in Oded Galor's *The Journey of Humanity*.

## Layout

```
data/            public by construction
  works.json       one record per work (id, authors, year, title, venue, type, doi, status, claims, OpenAlex data)
  citations.json   one row per endnote citation: {work, book, loc, flag}
  books.json       one record per book: chapters, its References list, audit notes
private/         gitignored, never committed or published (see private/README.md)
  notes.json       reading verdicts and notes
  ext.py           optional: your own extra commands
site/index.html  the page; `bib.py build` writes site/data/ next to it
inbox/           drop new books' extracted references here (TEMPLATE.json shows the format)
bib.py           CLI and importable module, standard library only
tests/           python -m unittest discover -s tests
infra/site.yml   CloudFormation: S3 + CloudFront + HTTPS + DNS + deploy role
```

Work ids are readable and stable: `alesina2013origins`, `acemoglu2001colonial`. Use them everywhere: in the CLI, in notes, and in links (`https://bib.yourdomain.com/#w-alesina2013origins`).

## Everyday commands

```bash
python bib.py search plough                     # find works
python bib.py show alesina2013origins           # full record, endnotes, private note
python bib.py stats                             # counts, cross-book overlap, most cited
python bib.py validate                          # integrity checks + possible duplicates

python bib.py tag alesina2013origins --status contested \
    --claim "Plough agriculture lowered female labour force participation"   # public
python bib.py tag alesina2013origins --verdict read --note "India: plough vs hoe districts"   # private
python bib.py claims -v                         # which claims rest on a single source

python bib.py serve                             # build and preview at http://localhost:8000
```

Statuses: `unchecked`, `replicated`, `contested`, `failed-replication`, `retracted`.

## Adding a book

1. Get the book's references as JSON in the shape of `inbox/TEMPLATE.json`. The easy way is to send Claude the epub and ask for the references in this format.
2. Preview the merge, then run it:
   ```bash
   python bib.py add-book inbox/henrich-2020.json --dry-run
   python bib.py add-book inbox/henrich-2020.json
   ```
   Works are matched by DOI, then by first author plus title. Exact matches merge automatically, including the same book reprinted in a different year. Near-misses, such as a working paper that was later published, are kept separate and flagged.
3. Resolve flags shown by `validate`: `python bib.py merge <keep> <drop>`, or `python bib.py not-dup <id>` if they really are different works.
4. Commit and push. The site updates on its own.

`python bib.py remove-book <id>` undoes an import, dropping works no other book references.

## OpenAlex enrichment

Adds DOIs, citation counts, field-weighted citation impact, top topics, and retraction flags. A retracted paper is marked `retracted` automatically.

```bash
export OPENALEX_API_KEY=...        # free at openalex.org/settings/api
python bib.py enrich --limit 20 -v # try a few first
python bib.py enrich               # then the rest; already-enriched works are skipped
```

Since February 2026 OpenAlex requires a key and meters usage against a free daily allowance. Lookups by DOI are free and the rest cost fractions of a cent, so a whole book fits well within the free tier. A match needs a close title, the same first author and a year within three (books can be any year). Anything unsure is left unmatched rather than guessed; add the DOI with `bib.py tag <id> --doi ...` and rerun.

## Private notes and extensions

`private/` is gitignored and never published. `bib.py tag --verdict/--note` writes there, and `show` displays it. To keep it in sync across devices, point it at a synced folder with `export BIB_PRIVATE_DIR=~/OneDrive/bibliography-private`.

To add your own commands without committing them, put an `ext.py` in that folder that defines `register(subparsers, handlers)`. `bib.py` loads it on startup.

## Publishing (AWS)

The site is served from a private S3 bucket through CloudFront at your subdomain, with HTTPS. Every push to `main` runs the tests, validates the data, builds the public files, uploads them and refreshes the CDN. The workflow uses short-lived credentials from an IAM role that can only write to this one bucket and refresh this one distribution, so no AWS keys are stored in GitHub. It also fails if anything in `private/` other than its README has been committed.

Cost: S3 storage for a site this size is a few cents a month, and CloudFront's free tier covers 1 TB of transfer per month. The Route 53 hosted zone you already pay for is the main cost.

### One-time setup

1. **Create the AWS resources.** `infra/site.yml` creates the bucket, the CloudFront distribution, the HTTPS certificate, the DNS records and the deploy role. Run it in `us-east-1`, which CloudFront requires for certificates:

   ```bash
   aws route53 list-hosted-zones --query "HostedZones[].[Name,Id]" --output text   # find your zone id

   aws cloudformation deploy --region us-east-1 \
     --stack-name bibliography-site \
     --template-file infra/site.yml \
     --capabilities CAPABILITY_NAMED_IAM \
     --parameter-overrides \
       DomainName=bib.yourdomain.com \
       HostedZoneId=Z0123456789ABC \
       GitHubRepo=rohit12/bibliography
   ```

   This takes about 5–10 minutes, mostly waiting for the certificate and CloudFront. If your account already has the GitHub OIDC provider, add `CreateGitHubOIDCProvider=false`.

2. **Tell GitHub where to deploy.** Print the stack outputs:

   ```bash
   aws cloudformation describe-stacks --region us-east-1 --stack-name bibliography-site \
     --query "Stacks[0].Outputs" --output table
   ```

   In the repository's Settings → Secrets and variables → Actions → **Variables**, add `AWS_ROLE_ARN`, `S3_BUCKET` and `CLOUDFRONT_DISTRIBUTION_ID` from those outputs. These are identifiers, not secrets.

3. **Push to `main`** (or run the workflow from the Actions tab). The site is live at `https://bib.yourdomain.com`.

## Programmatic access

From Python, in this repository:

```python
import bib
db = bib.DB.load()
w = db.works["acemoglu2001colonial"]
db.books_for(w["id"])                   # ['galor-2022', ...]
[c for c in db.citations if c["work"] == w["id"]]
```

From anywhere, once deployed (public fields only):

```bash
curl -s https://bib.yourdomain.com/data/works.json | jq '.[] | select(.books | length > 1) | .id'
curl -s https://bib.yourdomain.com/data/bibliography.bib > refs.bib
```

Public files: `works.json`, `citations.json`, `books.json`, `bibliography.bib`, `bibliography.csv`, `meta.json`. Exports for a single book: `python bib.py export bibtex --book galor-2022 -o galor.bib` (also `csv` and `csl`).

## What is public

`bib.py build` publishes only these work fields: id, authors, year, title, venue, type, citation, doi, status, claims, and OpenAlex id, citation count, FWCI, retraction flag and topics. Anything else you add to a work stays in `data/works.json`, which is public in this repository, but never reaches the site. Put anything you want kept to yourself in a private note: `bib.py tag <id> --note ...`. After building, it checks every file in `site/` for text from `private/`; if it finds any, it deletes the generated files and fails, so nothing deploys.

To rename the site, edit the `<title>` and `<h1>` in `site/index.html`.
