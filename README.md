# Bibliography

A cross-book bibliography: every work cited in a set of books, with the endnotes that cite it. It starts with the references in Oded Galor's *The Journey of Humanity* (2022).

- `data/works.json`: one record per work, with the endnotes that cite it
- `data/books.json`: the books

The published site also serves these files, plus BibTeX and CSV exports, under `/data/`.

## Publishing

Run once, in `us-east-1`:

```bash
aws cloudformation deploy --region us-east-1 --stack-name bibliography-site \
  --template-file infra/site.yml --capabilities CAPABILITY_NAMED_IAM \
  --parameter-overrides DomainName=bib.example.com HostedZoneId=Z... GitHubRepo=owner/bibliography
```

Copy the stack outputs into the repository's Actions variables `AWS_ROLE_ARN`, `S3_BUCKET` and `CLOUDFRONT_DISTRIBUTION_ID`. After that, every push to `main` deploys.
