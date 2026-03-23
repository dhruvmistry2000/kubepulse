
# Contributing

Contributions to KubePulse are welcome! Whether it's a bug fix, a new feature, improved documentation, or a test — all contributions are appreciated.

---

## Getting Started

1. **Fork** the repository on GitHub.
2. **Clone** your fork locally:

```bash
git clone https://github.com/YOUR_USERNAME/kubepulse.git
cd kubepulse
```

3. Create a **feature branch**:

```bash
git checkout -b feature/my-new-feature
# or
git checkout -b fix/issue-description
```

4. Make your changes, commit them, and push to your fork.
5. Open a **Pull Request** against the `main` branch of the upstream repository.

---

## Development Setup

### Dashboard (local)

```bash
cd dashboard
pip install flask kubernetes
python app.py
```

The dashboard will start on `http://localhost:8080`.

### Agent (local, requires a running cluster)

```bash
cd agent
pip install flask kubernetes requests
# Set required environment variables
export DASHBOARD_URL=http://localhost:8080
export REGISTRATION_TOKEN=your-token
export CLUSTER_NAME=local-dev
python app.py
```

### Building Images Locally

```bash
# Dashboard
docker build -t kubepulse-dashboard:dev ./dashboard

# Agent
docker build -t kubepulse-agent:dev ./agent
```

---

## Code Standards

- **Python style:** Follow [PEP 8](https://peps.python.org/pep-0008/). Use 4-space indentation.
- **HTML/CSS/JS:** Keep all frontend code in `dashboard/templates/index.html`. Prefer readability over brevity.
- **Helm templates:** Follow the [Helm best practices guide](https://helm.sh/docs/chart_best_practices/). Use `{{ include }}` helpers for repeated labels.
- **Commit messages:** Use the conventional format: `type(scope): description` — for example, `fix(agent): handle heartbeat timeout gracefully` or `feat(dashboard): add namespace filter to graphs tab`.
- **No secrets in code:** Never hardcode `SECRET_KEY`, `ADMIN_KEY`, or any credentials in source files.

---

## Pull Request Guidelines

- **Open an issue first** for significant changes to discuss the design before writing code.
- **Keep PRs focused.** One feature or fix per PR makes reviewing faster.
- **Write a clear PR description.** Explain what problem you're solving and how.
- **Test your changes locally** before opening a PR. Verify the dashboard UI works, the agent registers, and heartbeats succeed.
- **Update documentation** if your change affects usage, configuration, or deployment.
- **Check for breaking changes.** If you're changing API endpoints, Helm values, or environment variable names, call it out explicitly in the PR description.

---

## Reporting Issues

When filing a bug report, please include:

- KubePulse version / commit SHA
- Kubernetes version and cloud provider (GKE / EKS / AKS / other)
- Dashboard hosting method (Cloud Run / Docker / in-cluster)
- Relevant log output from the agent and/or dashboard
- Steps to reproduce

---

## Project Roadmap Ideas

If you're looking for ways to contribute, consider these areas:

- Persistent storage backend for the dashboard (Redis or SQLite) to survive restarts
- Helm chart for the dashboard itself (currently only the agent has a chart)
- Alert rules and notifications (Slack, email, webhook)
- RBAC for multi-user dashboard access
- Dark/light theme toggle
- Export cluster data to CSV or JSON