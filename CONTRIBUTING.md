> The files in this repo are GENERATED. The source of truth is a private
> repository (an export tool wrote this copy). There is no need to edit this
> repo (the next export would overwrite it). To suggest changes, open an
> issue or a pull request. A maintainer will review it, apply the changes
> to the private source manually, and then re-export. See CONTRIBUTING.md
> for more details.

# Contributing

Thanks for wanting to contribute! Here's what you need to know first.

## We can't merge pull requests here

An export tool writes every file in this repo from our private repo. We don't edit anything here by hand. If we did merge your PR, the next export would quietly wipe it out.

## Open PRs anyway though

Open a PR or an issue that says what should change and why. We'll make the change in the private repo and run the export. The changes should show up here in the next export. We can credit you in the export commit (if you'd rather not be named, say so and we'll leave you out). If we decide not to make a change, we'll tell you why on the PR.

## What to report

- Anything that's wrong. If a comment says a setting does something it doesn't, we want to know. The point of this repo is to be accurate.
- Anything you can't check. If `README.md` says something but nothing backs it up, tell us. We'll publish the file or tone down the claim.
- Anything that looks like a leak, like an internal address, an internal hostname, or anything that looks like a password or key. Please email <directactionvillage@proton.me> about it instead of opening a public issue.
- Typos and unclear wording on the subscriber pages.

## Checking a file is what we published

`MANIFEST.txt` has a sha256 for every file here (in `sha256sum` format):

```sh
sha256sum -c MANIFEST.txt
```

The same source always makes the same files. There's no timestamp or build ID in them, so any diff here is a real change.
