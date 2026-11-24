# bench-parts

Self-hosted parts inventory for a board-repair bench — its own tablet-friendly
app for managing stock, plus a REST API that [benchhud](https://github.com/IamMrCupp/benchhud)
and anything else on the LAN can consume.

Runs on the bench LAN. No cloud, no subscription. Sibling to
[benchhud-intake](https://github.com/IamMrCupp/benchhud-intake).

## Two faces

Both are first-class, and neither depends on the other:

- **A standalone inventory app.** A web UI you use directly from a tablet or
  laptop — add a part, adjust a count when you restock or use something, and
  check the **"to order"** list before a supplier run. Works on its own with
  benchhud not running.
- **A general parts REST API.** Consumer-agnostic: any authorized client on the
  LAN can read stock and record consumption. benchhud is the first consumer via
  an `InventorySource` adapter, but a dashboard, a widget, or a script could use
  the same endpoints with no server-side change.

Unlike its sibling, **no customer PII lives here** — parts data is stream-safe,
which is why benchhud can surface it on the HUD ("you're on 26-0001, low on
IRF7910"). Consumption links to a job by *reference string*; this service never
calls intake to validate it.

## Downloads

Every [release](https://github.com/IamMrCupp/bench-parts/releases) has its installers
attached: the Linux service `.deb` (amd64 and arm64), the Windows tray-app MSI
and the macOS tray app. They're built by the same CI jobs every PR runs, at the
release tag, including the install checks. The macOS app is a `.dmg` for Apple
silicon, signed with a Developer ID and notarized, so it opens without a
Gatekeeper warning.

```bash
gh release download --repo IamMrCupp/bench-parts --pattern '*_amd64.deb'
```

PRs and merges to `main` build and check only the Linux amd64 packages, since
macOS and Windows runners bill at 10× and 2×. The macOS, Windows and arm64 builds
run for every release, by hand, or on a PR labelled `full-ci` (use it on a PR
that touches packaging). Docs-only changes skip CI.

## The menu-bar app

On a Mac or Windows PC, the easiest way to run bench-parts is the app: it lives in the
menu bar (macOS) or system tray (Windows), with no window of its own.

- **First launch** asks for a password. It won't serve the inventory, or its API,
  without one.
- The menu shows whether it's running and where, with **Open in browser**,
  **Share on the LAN** (so a tablet can reach it), **Start at login**,
  **Set password…**, **New API token…** (copied to the clipboard for benchhud's
  Inventory tab), **Open data folder** and **Quit**.
- It uses the same data folder and `settings.env` as `bench-parts serve`,
  so you can switch between the two.

Download it from the latest [release](https://github.com/IamMrCupp/bench-parts/releases):
the `.dmg` (macOS, Apple silicon, signed and notarized) or the `.msi`
(Windows). Both are built for every release (and on a PR labelled
`full-ci`), and launched in a smoke mode that serves and answers `/healthz`
before they're attached. From a `uv tool` install, run it with
`bench-parts tray` (install the `tray` extra: `'bench-parts[tray] @ git+…'`).

On a Mac, [Homebrew](https://github.com/IamMrCupp/homebrew-bench) also installs it, and keeps it current with
`brew upgrade`:

```sh
brew tap iammrcupp/bench
brew install --cask bench-parts
```

**The whole bench on a Mac (optional).** If the same Mac runs the rest of the
bench, the tap's `bench-suite` cask installs benchhud, both menu-bar apps and
`bench-setup`:

```sh
brew tap iammrcupp/bench
brew trust --tap iammrcupp/bench
brew install --cask bench-suite
bench-setup
```

`bench-setup` sets one password for both apps, makes a token each, and writes
the URLs and tokens into benchhud's config, so there's no token to copy by
hand. macOS asks before the first launch of a download, so it will tell you to
open bench parts once from Applications, then run it again. Nothing here needs it.
See [bench-suite](https://github.com/IamMrCupp/bench-suite#on-a-mac).

To build the app yourself: `uvx briefcase create macOS app && uvx briefcase build macOS app`
(or `windows`). `scripts/make_icons.py` regenerates the icons.

## On a Linux box, as a service

For a headless bench machine or a Pi: a `.deb` that runs bench-parts under systemd.
CI builds it for `amd64` and `arm64` on every PR (`bench-parts-deb-amd64`,
`bench-parts-deb-arm64`), for Ubuntu 26.04.

```bash
sudo apt install ./bench-parts_<version>_amd64.deb
sudo bench-parts set-password     # it won't start without one
sudo bench-parts new-token        # for benchhud and scripts
sudo systemctl start bench-parts  # http://127.0.0.1:8402/
```

**Or from the bench apt repo (optional).** The same package is in the signed
apt repo that [bench-suite](https://github.com/IamMrCupp/bench-suite#readme)
publishes, so updates arrive with `apt upgrade`:

```bash
sudo curl -fsSLo /usr/share/keyrings/bench-archive-keyring.gpg https://apt.yolorepair.com/bench-archive-keyring.gpg
echo "deb [signed-by=/usr/share/keyrings/bench-archive-keyring.gpg] https://apt.yolorepair.com stable main" \
  | sudo tee /etc/apt/sources.list.d/bench.list
sudo apt update
sudo apt install bench-parts
```

If the same machine runs the rest of the bench, `sudo apt install bench-suite`
installs the lot, and `sudo bench-setup` does the setup above for every service
at once: one password, a token each, and the URLs and tokens written into
benchhud's config. `bench-setup doctor` checks the result. Nothing here needs it.

- It runs as its own `bench-parts` system user, with its data in
  `/var/lib/bench-parts`, and it's enabled to start at boot.
- It runs **sandboxed**: no capabilities, a read-only view of the system
  (`ProtectSystem=strict`) with only its data folder writable, its own `/tmp`,
  no devices, TCP and local sockets only, and systemd's `@system-service`
  syscall set. CI checks `systemd-analyze security` stays at or below **4.0**,
  so it can't quietly loosen.
- `settings.env` there stays **root-only**. systemd reads it and passes the
  password and token in, so the service user never can.
- It listens on loopback, port 8402, recorded as `BENCH_PARTS_PORT` in
  `settings.env` at install. A set-up install upgraded from before 8402 stays
  on 8000: the upgrade writes `BENCH_PARTS_PORT=8000` there. Edit that line to
  move it.
- For a tablet or benchhud on another machine, add
  `BENCH_PARTS_HOST=0.0.0.0` to
  `settings.env` and `sudo systemctl restart bench-parts`.
- With no password it fails once and stays stopped, rather than restarting in
  a loop. `journalctl -u bench-parts` says why.
- Removing the package keeps the data and the service user: deleting your
  inventory should never be a side effect of `apt remove`.

## Installing it as a program

No cluster needed. Install the command with [uv](https://docs.astral.sh/uv/)
(or pipx), set a password once, and run it:

```bash
uv tool install git+https://github.com/IamMrCupp/bench-parts
bench-parts set-password     # required: it won't serve without one
bench-parts new-token        # the API token for benchhud's Inventory tab and scripts
bench-parts serve            # http://127.0.0.1:8402/
```

`serve` stays on this machine by default. `--host 0.0.0.0` shares it on the LAN
so a tablet (or benchhud on another machine) can reach it. Keep it off public
networks.

For scripts (bench-suite's `bench-setup` uses these), nothing prompts and no
secret goes on the command line:

- `set-password --stdin` reads the password once from stdin, with no confirm.
- `set-password --generate` makes a random one and prints it once.
- `new-token --print` prints only the token on stdout.

Everything lives in one per-user folder. `bench-parts paths` shows it:

| OS | Data folder |
|---|---|
| macOS | `~/Library/Application Support/bench-parts` |
| Windows | `%APPDATA%\bench-parts` |
| Linux | `$XDG_DATA_HOME/bench-parts` (`~/.local/share/…`) |

It holds `parts.db` and `settings.env`, the owner-only settings file that
`set-password` and `new-token` write. `--data-dir` or `BENCH_PARTS_DATA_DIR`
points it elsewhere. **Environment variables always win over the settings
file**, so the container and the cluster deployment below work exactly as before.

Every page has a **Source** link in the footer, sign-in page included.
`BENCH_PARTS_SOURCE_URL` sets where it points and defaults to this repository.
If you run a modified copy for other people, point it at your own code — that's
the offer of source AGPL section 13 asks for. It wants an `http://` or
`https://` address; anything else is ignored and the link stays on this
repository.

## Looks and themes

Same look as benchhud, burnwright, benchhud-intake and bench-recon — same
palette, same panels. **Appearance**, at the foot of every page, picks System,
Light or Dark, and the choice sticks in that browser.

For your own, drop a burnwright theme file into the `themes` folder in the data
folder (`bench-parts paths` shows where), or point `BENCH_PARTS_THEMES_DIR` at a
folder of them. It shows up in the Appearance list on the next page load. The
format is burnwright's, so a theme you've written for it works here unchanged:

```json
{
  "name": "Amber terminal",
  "base": "dark",
  "colors": { "bg": "#120d02", "panel": "#1b1404", "fg": "#ffcf70", "accent": "#ffb000" }
}
```

`base` is `"light"` or `"dark"`; anything you leave out comes from it. Colours
only — hex, `rgb()` or `hsl()` — with burnwright's names (`bg`, `panel`,
`border`, `fg`, `muted`, `control-bg`, `control-border`, `control-hover`,
`accent`, `accent-soft`, `danger`, `danger-soft`, `warning`, `warning-soft`,
`warning-border`, `ok`, `ok-soft`). Stock status follows them: in stock is
`ok`, low is `warning`, out is `danger`. A file that can't be used is named at
the foot of the page, to anyone signed in, with the reason.

## Running it locally

Requires [uv](https://docs.astral.sh/uv/). Python 3.12 is pinned via
`.python-version` — uv will fetch it if you don't have it.

```bash
uv sync
uv run uvicorn bench_parts.app:app --reload
```

Then hit http://127.0.0.1:8000/ for the app, or `/healthz` for the probe.

## Development

```bash
uv run pytest                  # tests
uv run ruff check .            # lint
uv run ruff format .           # format
```

CI runs all three on every PR, plus a multi-arch container build. Both must be
green before merge.

The lint config is kept deliberately identical to benchhud-intake's, so
switching between the two repos costs nothing.

## Container

Images are on ghcr.io for `linux/amd64` and `linux/arm64`: `:latest` tracks
`main`, and each release is tagged `:X.Y.Z` and `:X.Y` so a deploy can pin one.

With Docker Compose (or `podman compose`), using the `compose.yaml` here:

```bash
docker compose run --rm parts bench-parts set-password   # once
docker compose run --rm parts bench-parts new-token      # for benchhud and scripts
docker compose up -d                                     # http://127.0.0.1:8402/
```

The data folder in the image is `/data` (`BENCH_PARTS_DATA_DIR`); compose
mounts a named volume there, so the database and `settings.env` survive
upgrades. The container starts with `bench-parts serve`, which reads
`settings.env` itself, so the `$` in the password hash is never touched by
Compose's variable expansion. Environment variables still win, which is how the
cluster passes its Secret.

To build it yourself:

```bash
docker build -t bench-parts .
```

The image is built for `linux/amd64` **and `linux/arm64`** — the deploy target is
a Raspberry Pi cluster, and an amd64-only mistake is invisible until the pod
refuses to schedule. CI asserts the published manifest advertises `linux/arm64`
on every push to `main`.

## Behind a reverse proxy

Failed logins are counted per client address, and the address is the TCP peer.
Put a proxy in front and every browser arrives from the proxy, so five wrong
passwords from any device lock sign-in for everyone for five minutes. The app
also believes it's on `http`, so the URLs in its API responses say `http://`.

`BENCH_PARTS_TRUSTED_PROXIES` fixes both. It's a comma-separated list of
addresses or CIDR ranges, in the environment or in `settings.env`:

```
BENCH_PARTS_TRUSTED_PROXIES=10.42.0.0/16
BENCH_PARTS_COOKIE_SECURE=1
```

When the connection comes from a listed address, `X-Forwarded-For` and
`X-Forwarded-Proto` are believed. From anyone else they're ignored. Unset,
nobody is believed, loopback included. `serve` and the menu-bar app both read
it.

- **Keep it as narrow as you can.** The proxy's own address, or on Kubernetes
  the ingress controller's pod range. Not the whole LAN. Anything inside a
  trusted range can send its own `X-Forwarded-For` and so choose its own
  throttle bucket, which is the lockout handed back to whoever wants around it.
- **A proxy on the same machine is listed like any other:**
  `BENCH_PARTS_TRUSTED_PROXIES=127.0.0.1`. Loopback gets no free pass, because
  on a one-machine install every browser is loopback and any of them could
  then pick its own bucket. If you ran nginx or Caddy on the same box before
  this setting existed, its forwarded headers were believed without asking;
  now it has to be listed.
- **`serve` refuses a list it can't stand behind**, with exit code 2 and the
  reason: `*`, a range that covers every address (`0.0.0.0/0`, `::/0`), or an
  entry that isn't an address or a range. A typo doesn't quietly mean "trust
  nothing" while the bench stays lockable. The menu-bar app shows the same
  reason and stays stopped.
- **It belongs with HTTPS.** If the proxy terminates TLS, set
  `BENCH_PARTS_COOKIE_SECURE=1` as well, so the session cookie is only sent
  over it. Don't set that without TLS: the browser would never send the cookie
  back and sign-in would fail silently.
- The proxy has to set the headers. nginx's ingress controller does by default.

## Deployment

**To run it on your own Kubernetes**, use the kustomize base in
[`deploy/kubernetes/`](deploy/kubernetes/README.md) from an overlay. It sets the
two things that must not change (one replica, Recreate) and leaves namespace,
storage and exposure to you.

The maintainer's own cluster runs from `cluster-management` (Flux GitOps),
alongside the other apps, following the same pattern as `apps/benchhud-intake`.
Merging to `main` here pushes
`ghcr.io/iammrcupp/bench-parts:latest`; Flux picks up the new digest and rolls
it out.

Two constraints that are easy to get wrong and painful to debug:

- **SQLite lives on an NFS volume**, so the app runs with `journal_mode=TRUNCATE`
  and `synchronous=FULL` — **not WAL**, which misbehaves on NFS and fails by
  corrupting rather than erroring.
- **Exactly one writer.** The Deployment must stay at `replicas: 1` with
  `strategy: Recreate`.

## License

Open source under the [GNU Affero General Public License v3.0](https://www.gnu.org/licenses/agpl-3.0.html)
or later — see [LICENSE](LICENSE). Use it, change it, ship it. If you distribute
a modified build, or run one as a service other people use, your changes go out
under the same licence.

Copyright © 2026 Aaron Cupp. If the AGPL doesn't fit how you want to use it, a
commercial licence is available — get in touch.
