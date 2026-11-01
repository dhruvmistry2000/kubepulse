# Local cluster

A one-node [kind](https://kind.sigs.k8s.io/) cluster managed by Terraform. It exists so you can `helm install` the kubepulse agent the same way you would on a real cluster. Terraform does not install the agent.

Cilium provides pod networking and replaces kube-proxy. The kernel this machine is currently booted into (`7.3.0-4-cachyos`) no longer has its netfilter modules on disk, so kube-proxy cannot program service rules. Cilium does that with BPF instead.

## Requirements

Docker, Terraform >= 1.5, kind, kubectl, and Helm 3 on `PATH`.

## Start

```bash
cd terraform/local
terraform init
terraform apply
```

`apply` creates the cluster and waits until CoreDNS is ready. Use the kubeconfig it writes:

```bash
export KUBECONFIG="$PWD/kubeconfig"
kubectl get nodes
```

`terraform output dashboard_url_from_pods` is the dashboard address pods can reach on the host. Put that in the agent install as `dashboardUrl`. Open the dashboard in the browser at `http://127.0.0.1:8765`.

## Shut it down

Stop the node and keep the cluster, including pulled images:

```bash
docker stop kubepulse-control-plane
```

Start the same cluster again:

```bash
docker start kubepulse-control-plane
```

Start it before the next `terraform apply` or `terraform destroy`. The kind provider reads credentials from the running node.

Remove the cluster:

```bash
terraform destroy
```

`apply` creates it again from this directory.
