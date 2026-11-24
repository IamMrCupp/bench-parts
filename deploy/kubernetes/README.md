# Kubernetes

A kustomize base for bench-parts. It's the shape the maintainer's own
cluster runs, without anything specific to that cluster: namespace, storage
class, exposure and image version are yours to set in an overlay.

## Before you apply

1. **Pull access.** The image is a private GHCR package. Create a pull secret in
   your namespace and add it in your overlay (see the example below).
2. **The Secret.** Fill in `secret.example.yaml` and apply it by hand. The app
   won't start without `BENCH_PARTS_PASSWORD_HASH`.

## An overlay

```yaml
# overlays/bench/kustomization.yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
namespace: bench-parts
resources:
  - https://github.com/IamMrCupp/bench-parts//deploy/kubernetes?ref=main
images:
  - name: ghcr.io/iammrcupp/bench-parts
    newTag: "0.3"                     # pin a release
patches:
  - target: {kind: Deployment, name: bench-parts}
    patch: |-
      - op: add
        path: /spec/template/spec/imagePullSecrets
        value: [{name: ghcr-pull}]
      # The volume is NFS: use the NFS-safe SQLite settings.
      - op: replace
        path: /spec/template/spec/containers/0/env/1/value
        value: "1"
  - target: {kind: PersistentVolumeClaim, name: bench-parts-data}
    patch: |-
      - op: add
        path: /spec/storageClassName
        value: nfs-client
  # LAN only: a fixed LoadBalancer IP, never a public ingress.
  - target: {kind: Service, name: bench-parts}
    patch: |-
      - op: add
        path: /spec/type
        value: LoadBalancer
      - op: add
        path: /spec/loadBalancerIP
        value: 192.168.1.50
```

Then `kubectl apply -k overlays/bench`.

## Behind an ingress

The overlay above exposes the Service directly, and needs nothing more. If you
put an ingress controller in front instead (LAN-only, terminating TLS), every
browser reaches the pod from the controller's address. Without the setting
below, five wrong passwords from any device lock sign-in for everyone for five
minutes, and the URLs in API responses say `http://`.

Add two variables in your overlay:

```yaml
patches:
  - target: {kind: Deployment, name: bench-parts}
    patch: |-
      # The ingress controller's pod range, as narrow as your cluster allows.
      - op: add
        path: /spec/template/spec/containers/0/env/-
        value: {name: BENCH_PARTS_TRUSTED_PROXIES, value: "10.42.0.0/16"}
      # TLS ends at the ingress, so the session cookie can be HTTPS-only.
      - op: add
        path: /spec/template/spec/containers/0/env/-
        value: {name: BENCH_PARTS_COOKIE_SECURE, value: "1"}
```

- **Narrow means the controller, not the LAN.** `X-Forwarded-For` is believed
  from any address in the list. The best value is the controller pod's own
  range: the node's pod CIDR if the controller is pinned to a node, the cluster
  pod CIDR if it can land anywhere. Never the LAN range the browsers are on.
- **Any pod inside a trusted range can choose its own throttle bucket** by
  sending the header itself. With the whole pod CIDR trusted, that's every
  workload in the cluster. A NetworkPolicy that admits only the ingress
  controller to port 8000 closes that.
- **The pod won't start on a bad list.** `*`, `0.0.0.0/0`, `::/0` or an entry
  that isn't an address or a range exits with code 2 and says why:
  `kubectl logs` shows the message, and the pod crashloops until it's fixed.
- **Set both or neither.** `BENCH_PARTS_COOKIE_SECURE=1` without HTTPS means
  the browser never sends the session cookie back, and sign-in fails silently.

## What not to change

- **`replicas: 1` and `strategy: Recreate`.** SQLite takes exactly one writer
  (stock counts are what a second one would get wrong), and the login rate
  limiter lives in one process's memory.
- **`BENCH_PARTS_SQLITE_NFS=1` on NFS.** The default WAL journal corrupts on NFS
  instead of failing.
- **No public exposure.** It's a read-write API for your inventory.

Backups aren't in the base. SQLite's `.backup` from a CronJob to a second
volume is the pattern the maintainer's cluster uses.
