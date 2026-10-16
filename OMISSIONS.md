> We generate the files in this repo from a private repo. Edits here would
> be overwritten by the next export. To suggest a change, open an issue or
> a pull request. We'll make it in the private repo and re-export. See
> CONTRIBUTING.md for details.

# What is not here

We only publish files we picked one by one. What's below is left out on
purpose.

We list categories instead of file paths (a list of paths would give away
too much about what we hold back).

## Details of our infrastructure

We leave out the details of the machines and networks this runs on.

A detail like that can lead an attacker to a machine or its owner. You don't need any of it to check what happens to your data. The files here still show what each part does.

## Firewall rule sets

We leave out the firewall rules on both machines and the template that builds the edge host's rules. We also leave out the list of machines allowed to reach the admin page.

The rules would show where our defenses are, mistakes included. The edge host drops anything we don't open. The engine's admin port is blocked to all but a few of our machines. The two machines only talk over the private link.

## Service resource limits

We leave out the template for Caddy's systemd limits, which set its memory ceiling, its restart delay, and its out-of-memory priority.

The numbers are in `config/listmonk_exposure/defaults.yml`. The template only applies them, and it would show how the edge host is built.

## Credential and key material paths

We leave out where the key files, API key files, and first-run password files live.

We don't keep secrets in this repo or the private one. But the paths would still tell someone who got onto a machine where to look.

## Internal tracker references

We leave out ticket, epic, and milestone numbers from our private tracker.

You can't open them, and they'd show how we organize our work.

## Administrative access configuration

We leave out the SSH server settings, the port it uses, and who has admin access.

That's how we log in (it doesn't touch your data). Admin access is key-only, with password logins turned off.

## Internal observability

We leave out our metrics exporter, what it collects from, how alerts get sent, and the inventory lookups behind them.

That's how we watch our own machines. We do publish the one check that touches the public site (it only loads a static file, and doesn't touch subscriber data).
