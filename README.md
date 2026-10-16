> We generate the files in this repo from a private repo. Edits here would
> be overwritten by the next export. To suggest a change, open an issue or
> a pull request. We'll make it in the private repo and re-export. See
> CONTRIBUTING.md for details.

# Direct Action Village mailing list configuration

This repo holds the config that runs the Direct Action Village mailing list. We publish it so anyone who gives us an email address can "check" what happens to that address. We put "check" in quotes because you can't really confirm this is the code running on our machines. Nobody who hosts a centralized service for you can prove that, unless you pay them a visit. So we're asking you to trust us, and we won't pretend anything here is a guarantee. You still have to make your own privacy and safety calls with your own data.

The list runs on a self-hosted [listmonk](https://listmonk.app) instance with two machines. The engine holds the database and sends the mail, and the internet can't reach it at all. The edge server holds the public name and the TLS certificate. It talks to the engine over one encrypted point-to-point link, and the engine opens that link. The edge server never starts the connection, and it never learns where the engine is.

## You can check...

Start with `config/listmonk_exposure/Caddyfile.j2`. It backs up each of these claims.

- The admin interface and the API aren't on the internet. The `@blocked` matcher returns `404` for `/admin*` and `/api*`. The one exception is `/api/public/captcha/altcha`, the anti-spam challenge for the signup form.
- We don't track opens or clicks. listmonk can, through `/link/*` and `/campaign/*`, but neither is on our allowlist, so both return `404`.
- We don't log your IP address at the edge. Caddy only writes an access log if there's a `log` directive, and we don't have one.

The allowlist is `listmonk_exposure_public_paths` in `config/listmonk_exposure/defaults.yml`. A path that isn't on it returns `404`.

## The grant application form

`/grants/apply` is a grant application form. It isn't part of the mailing list. listmonk only stores an email address, a name, and your lists, so a separate program handles the form. It's switched off for now. Both switches are `false`: `listmonk_grant_form_enabled` (engine) and `listmonk_exposure_grant_form_enabled` (edge server). Until we flip them, the path returns `404`. The exact URLs are in `listmonk_grant_form_paths`. The handler only listens on `127.0.0.1`, so nothing outside the engine's machine can reach it (`listmonk_grant_form_upstream`).

The edge server passes each application through and doesn't store it. It doesn't keep an access log either, so it never records your IP address. We haven't published the handler's code yet (`OMISSIONS.md` lists it). The engine checks the same allowlist again in `config/listmonk/Caddyfile.j2`, so someone who took over the edge server still couldn't reach anything else.

## Everything else

| Path | What it is |
| --- | --- |
| `config/listmonk/defaults.yml` | the engine's settings: the pinned version and its checksum, the public site name, the send-rate window, the backup retention, the liveness probe, the grant form's route and switch, and the link between the two machines (direction, keepalive, MTU, and the port) |
| `config/listmonk/Caddyfile.j2` | the engine's own copy of the path allowlist |
| `config/listmonk_exposure/defaults.yml` | the edge server's settings: TLS posture, HSTS, the path allowlist, the mail ports, and the grant form's switch |
| `config/listmonk_exposure/Caddyfile.j2` | the public front door |
| `pages/` | the pages a subscriber actually reads |
| `mail/` | the confirmation emails a subscriber actually gets |
| `OMISSIONS.md` | what we leave out of this repo, by category, and why |
| `MANIFEST.txt` | a sha256 for every file here, in `sha256sum -c` format |

The `.j2` files are Jinja2 templates. When we deploy, we fill in each `{{ name }}` from the `defaults.yml` next to it. Where we withhold a value, you can still see the rule that uses it.

## This repo isn't...

You can't deploy from it, because some of what a real deployment needs isn't here (`OMISSIONS.md` explains). We don't edit it by hand either. We generate every file here from a private repo. See `CONTRIBUTING.md` if you want something changed.

## Contact

Write to <directactionvillage@proton.me>.
