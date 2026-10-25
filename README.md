# Bibliography

A cross-book bibliography: every work cited in a set of books, with the endnotes that cite it.

- `data/works.json`: one record per work, with the endnotes that cite it
- `data/books.json`: the books

The published site also serves these files, plus BibTeX and CSV exports, under `/data/`.

## Publishing

Run once, in `us-east-1`:

```bash
aws cloudformation deploy --region us-east-1 --stack-name bibliography-site \
  --template-file infra/site.yml --capabilities CAPABILITY_NAMED_IAM \
  --parameter-overrides DomainName=bib.example.com HostedZoneId=Z... GitHubRepo=owner@OWNER_ID/bibliography@REPO_ID
```

To require a login, also pass `BasicAuthHash` (see its description in `infra/site.yml`). Put the stack outputs in the `env` block of `.github/workflows/deploy.yml`. After that, every push to `main` deploys.
