> We generate the files in this repo from a private repo. Edits here would
> be overwritten by the next export. To suggest a change, open an issue or
> a pull request. We'll make it in the private repo and re-export. See
> CONTRIBUTING.md for details.

# Contributing

Thanks for wanting to help! Here's what you need to know first.

## We can't merge pull requests here

We generate every file in this repo from a private repo, and we don't edit anything here by hand. If we merged your PR, the next export would quietly wipe it out.

## Open a PR anyway

Open a PR or an issue that says what should change and why. We'll make the change in the private repo and re-export, so it shows up here after the next export. We're happy to credit you in the export commit. If you'd rather not be named, say so and we'll leave you out. If we decide not to make a change, we'll tell you why on the PR.

## What to report

- Anything that's wrong. If a comment says a setting does something it doesn't, we want to know. The whole point of this repo is to be accurate.
- Anything you can't check. If `README.md` says something and nothing backs it up, tell us. We'll publish the file or tone down the claim.
- Anything that looks like a leak, like an internal address or hostname, a password, or a key. Please email <directactionvillage@proton.me> about it instead of opening a public issue.
- Typos and unclear wording on the subscriber pages.

## Checking a file is what we published

`MANIFEST.txt` has a sha256 for every file here, in `sha256sum` format:

```sh
sha256sum -c MANIFEST.txt
```

The same source always makes the same files. They don't have a timestamp or build ID, so any diff here is a real change.
