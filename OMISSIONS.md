> The files in this repo are GENERATED. The source of truth is a private
> repository (an export tool wrote this copy). There is no need to edit this
> repo (the next export would overwrite it). To suggest changes, open an
> issue or a pull request. A maintainer will review it, apply the changes
> to the private source manually, and then re-export. See CONTRIBUTING.md
> for more details.

# What is not here

We only publish files we picked one by one. What's below is left out on
purpose.

We list categories instead of file paths (a list of paths would give away
too much about what we hold back).

## Network addressing

We leave out every private (RFC 1918) address, the two /32s on the private link, the edge host's public address, and every internal DNS name.

An address tells an attacker where to look. You don't need any of it to check what happens to your data.

## Host and service names

We leave out container IDs, node names, workstation names, the internal DNS suffix, and the names of the internal services the list talks to.

A name can lead to an address and an owner. The files here still show what each machine does.

## Firewall rule sets

We leave out the firewall rules on both machines, their templates, and the list of machines allowed to reach the admin page.

The rules would show where our defenses are (mistakes included). The edge host drops anything we don't open. The engine's admin port is blocked to all but a few of our machines. The two machines only talk over the private link.

## Credential and key material paths

We leave out where the key files, API key files, and first-run password files live, and the provider account they go with.

We don't keep secrets in this repo or the private one. But the paths would still tell someone who got onto a machine where to look.

## Providers and accounts

We leave out our hosting provider, our account tier, and the VPN our private site uses to reach the internet.

A provider and a public address together could identify us. The edge host is a small box that does one job.

## Internal tracker references

We leave out ticket, epic, and milestone numbers from our private tracker.

You can't open them, and they'd show how we organize our work.

## Administrative access configuration

We leave out the SSH server settings, the port it uses, and who has admin access.

That's how we log in (it doesn't touch your data). Admin access is key-only, with password logins turned off.

## The grant application handler

We leave out the code for the program that serves and stores grant applications, and the form pages. We do publish its path, its switch, and the loopback address it listens on (in the config files here).

We plan to publish it, but haven't decided whether to add a second program to this repo yet. The form is switched off in these files, so it can't take applications right now.

## Internal observability

We leave out our metrics exporter, what it collects from, how alerts get sent, and the inventory lookups behind them.

That's how we watch our own machines. We do publish the one check that touches the public site (it only loads a static file, and doesn't touch subscriber data).
