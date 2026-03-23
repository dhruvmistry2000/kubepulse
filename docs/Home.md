# KubePulse GitHub Wiki

# 🫀 KubePulse

> A lightweight, push-based Kubernetes monitoring dashboard for single and multi-cluster environments.

[![Kubernetes](https://img.shields.io/badge/Kubernetes-1.24+-326CE5?logo=kubernetes&logoColor=white)](https://kubernetes.io)
[![Python](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white)](https://python.org)
[![Helm](https://img.shields.io/badge/Helm-3.x-0F1689?logo=helm&logoColor=white)](https://helm.sh)
[![GKE](https://img.shields.io/badge/GKE-supported-4285F4?logo=googlecloud&logoColor=white)](https://cloud.google.com/kubernetes-engine)
[![EKS](https://img.shields.io/badge/EKS-supported-FF9900?logo=amazonaws&logoColor=white)](https://aws.amazon.com/eks)
[![AKS](https://img.shields.io/badge/AKS-supported-0078D4?logo=microsoftazure&logoColor=white)](https://azure.microsoft.com/en-us/products/kubernetes-service)

---

## Overview

**KubePulse** is a self-hosted Kubernetes monitoring tool that provides real-time visibility into your clusters through a clean, modern web UI. A small **agent** Helm chart is deployed inside each cluster; it continuously pushes live data outbound to a central **dashboard**. No inbound firewall rules, no LoadBalancer exposure on your clusters — just secure, outbound-only HTTPS communication.

KubePulse is designed to be simple to deploy, easy to operate, and practical for teams running one or many Kubernetes clusters across different cloud providers.

---

## Key Features

- **Push-based architecture** — Agents push data to the dashboard. No inbound ports required on your clusters.
- **Multi-cluster support** — Monitor GKE, EKS, AKS, and other Kubernetes clusters from a single dashboard.
- **One-time registration tokens** — Secure, time-limited (10-minute) bootstrap flow for each cluster.
- **Real-time metrics** — Pod CPU/memory usage, deployment health, node status, and more, refreshed every 30 seconds.
- **Namespace-aware analytics** — Per-namespace graphs, filters, and resource breakdowns.
- **System namespace filtering** — Automatically hides `kube-system` and similar namespaces by default.
- **Modern UI** — Dark-themed single-page app with expandable rows, cross-linking between resources, and silent background refresh.
- **Pre-built container images** — Available on Docker Hub and GitHub Container Registry (GHCR). No build step required for standard use.
- **Helm deployment** — Agent is packaged as a Helm chart for repeatable, configurable installs.

---

## Use Cases

| Scenario | How KubePulse Helps |
|---|---|
| **Multi-cloud / multi-cluster operations** | View all clusters — GKE, EKS, AKS — in a single dashboard without VPN or network peering. |
| **Platform engineering teams** | Provide developers a simple, read-only view of workload health without exposing raw `kubectl` access. |
| **Staging / production monitoring** | Keep tabs on CPU, memory, and pod health across environments from one URL. |
| **Private / restricted clusters** | Works with clusters that have no inbound internet access — only outbound HTTPS required. |
| **Small teams with no Prometheus** | A lightweight alternative to heavy observability stacks for teams that just need pod/node health at a glance. |

---

## Architecture Summary

KubePulse has two components:

- **Dashboard** — A Flask web application hosted on any HTTPS-accessible server (Cloud Run, VM + nginx, or Kubernetes). It serves the browser UI, issues registration tokens, and stores the latest cluster state in memory.
- **Agent** — A lightweight Flask application deployed via Helm into each monitored cluster. It reads the Kubernetes API using a read-only ServiceAccount, then pushes data to the dashboard every 30 seconds via `POST /api/heartbeat`.

```
Browser → HTTPS polling → Dashboard ← HTTPS heartbeat ← Agent (in-cluster)
```

See [[Architecture]] for the full design breakdown.

---

## Quick Links

- [[Getting Started]] — Install and connect your first cluster in minutes.
- [[Architecture]] — Deep dive into system design and data flow.
- [[Usage Guide]] — Dashboard features, API endpoints, and common workflows.
- [[Configuration]] — All environment variables and Helm values.
- [[Deployment]] — Cloud Run, Docker VM, and in-cluster deployment options.
- [[Troubleshooting]] — Common issues and how to fix them.
- [[Contributing]] — How to contribute code and improvements.
- [[FAQ]] — Frequently asked questions.