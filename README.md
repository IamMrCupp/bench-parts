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
release tag, including the install checks. The macOS app is ad-hoc signed, so
the first launch needs right-click → Open.

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
the `.app.zip` (macOS, ad-hoc signed, so right-click → Open the first time) or
the `.msi` (Windows). Both are built for every release (and on a PR labelled
`full-ci`), and launched in a smoke mode that serves and answers `/healthz`
before they're attached. From a `uv tool` install, run it with
`bench-parts tray` (install the `tray` extra: `'bench-parts[tray] @ git+…'`).

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

Source-available under the [PolyForm Small Business License 1.0.0](https://polyformproject.org/licenses/small-business/1.0.0/)
— see [LICENSE](LICENSE). Free to use, modify and distribute if your company has
fewer than 100 people and under $1M in annual revenue, which covers personal use
and most small shops. Above that, a commercial licence is required — get in touch.

Note this is *source-available*, not open source: the code is readable and
freely usable within those limits, but it isn't an OSI-approved licence.
