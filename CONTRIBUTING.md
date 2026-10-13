# Contributing to Flexy

Start with the [project guide](docs/PROJECT_GUIDE.md) and [source reference](docs/REFERENCE.md). They describe the current entry points, commands, configuration, and known limitations.

## Workflow

1. Create a branch from the repository's default branch, `main`.
2. Reproduce the relevant behavior using the documented setup and disposable data where persistence is involved.
3. Make a focused change and update documentation whenever setup, configuration, interfaces, or behavior changes.
4. Run the relevant checks named in the guide. If a check needs unavailable services or is a known placeholder, report that explicitly instead of claiming it passed.
5. Open a pull request describing the problem, resulting behavior, validation performed, and any remaining limitations.

## Documentation maintenance

Keep command names aligned with their manifest and working directory. Update route/configuration references alongside source changes. Use relative links to tracked files, preserve project credits and license notices, and describe planned features as planned. Avoid publishing credentials, database exports, personal records, or generated caches in examples.

For a bug report, include reproduction steps, expected and actual results, runtime versions, and a minimal redacted error. For documentation edits, verify the commands against source and check every new local link; a full application build is only needed if the edit changes or depends on runtime behavior.
