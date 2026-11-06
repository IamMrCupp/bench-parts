# Changelog

## [0.3.0](https://github.com/IamMrCupp/bench-parts/compare/v0.2.2...v0.3.0) (2026-11-06)


### Features

* **assets:** add asset JSON API (list, assign, return) ([76fef26](https://github.com/IamMrCupp/bench-parts/commit/76fef26794c0bbede8f61d37e0166773077f1e62))
* **assets:** add asset model and assign/return store (schema v6) ([ee20cbb](https://github.com/IamMrCupp/bench-parts/commit/ee20cbb86141e0723b461d743fbceedbd5d9b178))
* **assets:** add asset web UI (catalogue, add/edit, assign/return) ([0eaede8](https://github.com/IamMrCupp/bench-parts/commit/0eaede89814f3851d81202f1f91d6e4746987ce0))
* **assets:** asset JSON API (list, assign, return) ([84cdc56](https://github.com/IamMrCupp/bench-parts/commit/84cdc56dbc9db3fed86e7470489456e6eb45ac30))
* **assets:** asset model and assign/return store (schema v6) ([aea574f](https://github.com/IamMrCupp/bench-parts/commit/aea574f966b5508da2b437877d5747892b2a74a9))
* **assets:** asset web UI (catalogue, add/edit, assign/return) ([837a1a9](https://github.com/IamMrCupp/bench-parts/commit/837a1a977590215e2f6007a2ddc66db90b854dab))
* **assets:** seed real bench test gear (idempotent loader) ([f39b2cf](https://github.com/IamMrCupp/bench-parts/commit/f39b2cfe772854a304529447b43d65fcce0d9c5b))
* **assets:** seed real bench test gear (idempotent loader) ([3f02133](https://github.com/IamMrCupp/bench-parts/commit/3f02133e2280a2e9ab516abaecc761f8da115371))

## [0.2.2](https://github.com/IamMrCupp/bench-parts/compare/v0.2.1...v0.2.2) (2026-10-12)


### Bug Fixes

* **release:** download only the installer artifacts, not the image build record ([46a367c](https://github.com/IamMrCupp/bench-parts/commit/46a367c6278c289f583e25042b8fd715beffa8ff))


### Documentation

* **readme:** the tray apps come from releases, not every PR ([a479984](https://github.com/IamMrCupp/bench-parts/commit/a47998444d8b717f364b0c8f903cabb307a9c039))

## [0.2.1](https://github.com/IamMrCupp/bench-parts/compare/v0.2.0...v0.2.1) (2026-09-29)


### Documentation

* **readme:** describe the service's systemd sandbox ([ef85ce5](https://github.com/IamMrCupp/bench-parts/commit/ef85ce5fce700db9b082150112e29353a4891632))

## [0.2.0](https://github.com/IamMrCupp/bench-parts/compare/v0.1.0...v0.2.0) (2026-09-26)


### Features

* **cli:** run without a cluster via a serve command, a data folder and a settings file ([031cafb](https://github.com/IamMrCupp/bench-parts/commit/031cafb6f189e45d39b8581a352c04636cd00fdf))
* **deb:** a .deb that runs bench-parts as a systemd service ([757396b](https://github.com/IamMrCupp/bench-parts/commit/757396bad87d619b9542472115a9a75ec5561706))
* **deploy:** a reusable kustomize base for Kubernetes ([8ef25cd](https://github.com/IamMrCupp/bench-parts/commit/8ef25cdebb89a3d8c43aa76cfd1cc413660f35ef))
* **image:** versioned release images, a compose file, and serve as the container entrypoint ([5b4fdd5](https://github.com/IamMrCupp/bench-parts/commit/5b4fdd5c3a9a7fb789b555d062ae7fd773c1cc25))
* **tray:** a menu-bar / tray app for macOS and Windows ([10882e1](https://github.com/IamMrCupp/bench-parts/commit/10882e181bd380ded2581cbf2aaa2436ad6686ca))

## 0.1.0 (2026-09-23)

Baseline: everything on `main` before release automation. Releases after this
one are cut by release-please from conventional commits.
