> The files in this repo are GENERATED. The source of truth is a private
> repository (an export tool wrote this copy). There is no need to edit this
> repo (the next export would overwrite it). To suggest changes, open an
> issue or a pull request. A maintainer will review it, apply the changes
> to the private source manually, and then re-export. See CONTRIBUTING.md
> for more details.

# Direct Action Village mailing list configuration

This repo holds the config that runs the Direct Action Village mailing list. We maintain this so that anyone who gives us an email address can "check" what happens to that address. We say "check" because realistically you won't be able to *actually* check that the published code is the same code that's running on our machines. Nobody who hosts centralized online services for you can prove that (unless you pay them a visit)! We're asking you to trust us, but also being honest that nothing we can say or show you is a guarantee. At the end of the day, you still need to make your own privacy and safety decisions with your own data.

The mailing list runs on a self-hosted [listmonk](https://listmonk.app) instance. An engine holds the database and sends the mail (not reachable from the internet at all). A small single-purpose edge server holds the public name and the TLS certificate. It reaches the engine over one point-to-point encrypted link that the engine dials out to establish. The edge server never initiates the connection and never learns where the engine is.

## You can check...

Start with `config/listmonk_exposure/Caddyfile.j2`, which specifies:

- The admin interface and the API aren't on the internet. The `@blocked` matcher returns `404` for `/admin*` and `/api*`. The one exception is `/api/public/captcha/altcha` (the anti-spam challenge for the signup form).
- We don't track opens or clicks. listmonk can do that through `/link/*` and `/campaign/*`, but we don't allowlist either of those, so both return `404`.
- We don't log your IP address at the edge. Caddy only writes an access log if there's a `log` directive, and we don't specify one.

The allowlist is `listmonk_exposure_public_paths` in `config/listmonk_exposure/defaults.yml`. If a path isn't specified in the allowlist, then it will `404`.

## The grant application form

`/grants/apply` is a grant application form (not part of the mailing list). listmonk can only store an email address, a name, and your lists, so a separate program handles the form. It's switched off right now. Both switches are `false`: `listmonk_grant_form_enabled` (engine) and `listmonk_exposure_grant_form_enabled` (edge server). Until we flip them, the path will return `404`. You can see the exact URLs in `listmonk_grant_form_paths`. The handler only listens on `127.0.0.1`, so anything outside the engine's machine can't reach it (`listmonk_grant_form_upstream`).

The edge server passes each application through without storing it. It also doesn't keep an access log, so it doesn't record your IP address either. We haven't published the handler's code yet (`OMISSIONS.md` lists it). The engine checks the same allowlist again in `config/listmonk/Caddyfile.j2` (so someone who took over the edge server still couldn't reach anything else).

## Everything else

| Path | What it is |
| --- | --- |
| `config/listmonk/defaults.yml` | the engine's settings: the pinned version and its checksum, the public site name, the send-rate window, the backup cadence, the liveness probe, the grant form's route and switch, and the link between the two machines (direction, keepalive, MTU, and the port) |
| `config/listmonk/Caddyfile.j2` | the engine's own copy of the path allowlist |
| `config/listmonk_exposure/defaults.yml` | the edge server's settings: TLS posture, HSTS, the path allowlist, the mail ports, and the grant form's switch |
| `config/listmonk_exposure/Caddyfile.j2` | the public front door |
| `pages/` | the pages a subscriber actually reads |
| `OMISSIONS.md` | what is withheld from this repository, by category, and why |
| `MANIFEST.txt` | a sha256 for every file here, in `sha256sum -c` format |

The `.j2` files are Jinja2 templates. We fill in each `{{ name }}` from the `defaults.yml` next to it when we deploy. Where we withhold a value, you can still see the rule that uses it.

## This repo isn't...

You can't deploy from it. Some of what a real deployment needs isn't here (explained in `OMISSIONS.md`). We don't edit this repo by hand either. An export tool writes every file here from our private repo. See `CONTRIBUTING.md` if you want something changed.

## Contact

Write to <directactionvillage@proton.me>.
