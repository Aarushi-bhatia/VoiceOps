# Kubernetes deployment

Sixteen objects across seven files. Apply them in order (or all at once — the
namespace is created first alphabetically by filename and `kubectl apply -f k8s/`
retries objects whose namespace does not exist yet).

```bash
kubectl apply -f k8s/
kubectl -n voiceops rollout status deploy/api deploy/worker deploy/dashboard
```

## What's here

| File | Objects | Notes |
|---|---|---|
| `namespace.yaml` | Namespace | Everything is namespaced to `voiceops` |
| `config.yaml` | ConfigMap, Secret | Non-secret settings and credential placeholders |
| `postgres.yaml` | StatefulSet, Service, PVC | The permanent record |
| `redis.yaml` | StatefulSet, Service, PVC | The queue, with append-only persistence |
| `api.yaml` | Deployment (×2), Service | Stateless, so it scales freely |
| `worker.yaml` | Deployment (×3), HPA | Autoscales on CPU, 3→20 pods |
| `dashboard.yaml` | Deployment (×2), Service, Ingress | nginx serving the built dashboard |

## Decisions worth knowing

**Postgres and Redis are StatefulSets, everything else is a Deployment.**
Stateful things need stable identity and their own storage. The API and workers
hold nothing locally, which is exactly why they can be scaled and replaced at will.

**Workers get a 120-second termination grace period.**
A call can run for minutes. The worker drains on SIGTERM, finishing in-flight
calls rather than cutting customers off mid-sentence. Without this, every deploy
would drop live conversations.

**The HPA scales in slowly** (a 300-second stabilisation window). Calls are
long-running, so aggressive scale-in would kill pods mid-conversation.

**Liveness and readiness probes check different things.** Liveness only asks
whether the process is alive. Readiness hits `/health/ready`, which verifies
Postgres and Redis are actually reachable — so a pod that has lost its database
is removed from the load balancer instead of serving errors.

**Redis runs with `--appendonly yes`.** The database can rebuild the queue after
a total loss, but that costs a restart pass; persistence means scheduled retries
survive an ordinary pod restart.

**Secrets are placeholders.** In a real cluster these come from a sealed secret
or an external secrets operator, never from a file in the repository.

## Running it locally

Needs a cluster. With Docker Desktop, enable Kubernetes in Settings, then:

```bash
docker build -t voiceops-backend:latest ./backend
docker build -t voiceops-dashboard:latest ./frontend
kubectl apply -f k8s/
echo "127.0.0.1 voiceops.local" | sudo tee -a /etc/hosts
```

The manifests use `imagePullPolicy: IfNotPresent` so a locally built image is
used rather than pulled from a registry.

## Status

**These manifests have not been applied to a live cluster.** They are valid YAML
and the object shapes are correct, but they have not been run. Treat them as the
intended deployment, not a proven one.
